package com.joseph.notes

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.view.View
import android.view.ViewGroup
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File

/**
 * A native shell around the local-first Notes web app.
 *
 * The web app is served from APK assets through WebViewAssetLoader on the
 * reserved appassets.androidplatform.net origin (never file://), so IndexedDB,
 * OPFS, the service worker, ES modules and the existing storage all behave as
 * one stable origin across launches.
 *
 * Native work is deliberately narrow:
 *   - SAF file picker + camera for note attachments;
 *   - a real email intent with an optional CSV/ZIP attachment;
 *   - AlarmManager notifications for the next reminder occurrence.
 */
class MainActivity : ComponentActivity() {

    companion object {
        const val ASSET_HOST = "appassets.androidplatform.net"
        const val START_URL = "https://$ASSET_HOST/assets/www/index.html"
    }

    private lateinit var webView: WebView
    private lateinit var assetLoader: WebViewAssetLoader
    private lateinit var cameraDir: File

    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var captureCallback: ValueCallback<Array<Uri>>? = null
    private var captureFile: File? = null
    private var captureUri: Uri? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val callback = fileChooserCallback
            fileChooserCallback = null
            if (callback == null) return@registerForActivityResult
            if (result.resultCode != Activity.RESULT_OK) {
                callback.onReceiveValue(null)
                return@registerForActivityResult
            }
            val data = result.data
            val uris = ArrayList<Uri>()
            val clip = data?.clipData
            if (clip != null) {
                for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { uris.add(it) }
            } else {
                data?.data?.let { uris.add(it) }
            }
            callback.onReceiveValue(if (uris.isEmpty()) null else uris.toTypedArray())
        }

    private val captureLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val callback = captureCallback
            val uri = captureUri
            captureCallback = null
            captureUri = null
            if (result.resultCode == Activity.RESULT_OK && uri != null) {
                callback?.onReceiveValue(arrayOf(uri))
            } else {
                captureFile?.delete()
                callback?.onReceiveValue(null)
            }
            captureFile = null
        }

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                Toast.makeText(
                    this,
                    "Reminders will still be listed, but Android notifications are off.",
                    Toast.LENGTH_LONG
                ).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        cameraDir = File(cacheDir, "camera").apply { mkdirs() }
        ReminderScheduler.createChannel(this)

        val root = FrameLayout(this).apply { setBackgroundColor(0xFFF5F5F5.toInt()) }
        webView = WebView(this)
        root.addView(
            webView,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        setContentView(root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            view.updatePadding(
                left = bars.left,
                top = bars.top,
                right = bars.right,
                bottom = bars.bottom
            )
            WindowInsetsCompat.CONSUMED
        }
        WindowCompat.getInsetsController(window, root).apply {
            isAppearanceLightStatusBars = true
            isAppearanceLightNavigationBars = true
        }

        assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        configureWebView()
        clearCacheOnUpgrade()
        askForNotificationPermission()

        webView.loadUrl(START_URL)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                webView.evaluateJavascript(
                    "(window.__notesHandleBack && window.__notesHandleBack()) === true"
                ) { value ->
                    if (value != "true") finish()
                }
            }
        })
    }

    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = false
            allowContentAccess = true
            allowFileAccessFromFileURLs = false
            allowUniversalAccessFromFileURLs = false
            useWideViewPort = true
            loadWithOverviewMode = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = true
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setGeolocationEnabled(false)
            textZoom = 100
            cacheMode = WebSettings.LOAD_DEFAULT
        }
        webView.overScrollMode = View.OVER_SCROLL_NEVER

        webView.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest?
            ): WebResourceResponse? {
                val url = request?.url ?: return null
                if (url.scheme == "https" && url.host == ASSET_HOST) {
                    return assetLoader.shouldInterceptRequest(url) ?: notFound()
                }
                // Optional Drive/GitHub backup transports use fetch through the
                // normal WebView network stack. No page can navigate away: that
                // is blocked in shouldOverrideUrlLoading below.
                return null
            }

            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean {
                val url = request?.url ?: return true
                if (url.scheme == "https" && url.host == ASSET_HOST) return false
                if (url.scheme == "mailto") {
                    openMailUri(url)
                    return true
                }
                // Never let the WebView become an arbitrary browser.
                return true
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                val callback = filePathCallback ?: return false
                fileChooserCallback?.onReceiveValue(null)
                fileChooserCallback = null

                if (fileChooserParams?.isCaptureEnabled == true && wantsImageOnly(fileChooserParams)) {
                    return capturePhoto(callback)
                }

                val intent = fileChooserParams?.createIntent()
                    ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                        type = "*/*"
                        addCategory(Intent.CATEGORY_OPENABLE)
                    }
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                if (fileChooserParams?.mode == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }

                fileChooserCallback = callback
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: ActivityNotFoundException) {
                    fileChooserCallback = null
                    callback.onReceiveValue(null)
                    Toast.makeText(
                        this@MainActivity,
                        "No file picker is available",
                        Toast.LENGTH_SHORT
                    ).show()
                    true
                }
            }

            override fun onJsAlert(
                view: WebView?,
                url: String?,
                message: String?,
                result: JsResult?
            ): Boolean {
                val jsResult = result ?: return false
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> jsResult.confirm() }
                    .setOnCancelListener { jsResult.cancel() }
                    .show()
                return true
            }

            override fun onJsConfirm(
                view: WebView?,
                url: String?,
                message: String?,
                result: JsResult?
            ): Boolean {
                val jsResult = result ?: return false
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> jsResult.confirm() }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> jsResult.cancel() }
                    .setOnCancelListener { jsResult.cancel() }
                    .show()
                return true
            }

            override fun onJsPrompt(
                view: WebView?,
                url: String?,
                message: String?,
                defaultValue: String?,
                result: JsPromptResult?
            ): Boolean {
                val jsResult = result ?: return false
                val input = EditText(this@MainActivity).apply {
                    setText(defaultValue ?: "")
                    setSelection(text.length)
                }
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setView(input)
                    .setPositiveButton(android.R.string.ok) { _, _ ->
                        jsResult.confirm(input.text.toString())
                    }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> jsResult.cancel() }
                    .setOnCancelListener { jsResult.cancel() }
                    .show()
                return true
            }
        }

        webView.addJavascriptInterface(
            NotesBridge(this, webView),
            "NotesNative"
        )
    }

    private fun wantsImageOnly(params: WebChromeClient.FileChooserParams): Boolean {
        val types = params.acceptTypes.filter { it.isNotBlank() }
        if (types.isEmpty()) return false
        return types.all { it.startsWith("image/") }
    }

    private fun capturePhoto(callback: ValueCallback<Array<Uri>>): Boolean {
        val file = File(cameraDir, "capture-${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        captureCallback?.onReceiveValue(null)
        captureCallback = callback
        captureFile = file
        captureUri = uri

        val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
            putExtra(MediaStore.EXTRA_OUTPUT, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            clipData = ClipData.newRawUri("output", uri)
        }
        return try {
            captureLauncher.launch(intent)
            true
        } catch (e: ActivityNotFoundException) {
            captureCallback = null
            captureFile?.delete()
            captureFile = null
            captureUri = null
            callback.onReceiveValue(null)
            Toast.makeText(this, "No camera app is available", Toast.LENGTH_SHORT).show()
            true
        }
    }

    private fun openMailUri(uri: Uri) {
        val intent = Intent(Intent.ACTION_SENDTO, uri)
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "No email app is installed", Toast.LENGTH_SHORT).show()
        }
    }

    private fun askForNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            == PackageManager.PERMISSION_GRANTED) return
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun clearCacheOnUpgrade() {
        val prefs = getSharedPreferences("notes", MODE_PRIVATE)
        if (prefs.getInt("versionCode", -1) != BuildConfig.VERSION_CODE) {
            webView.clearCache(true)
            prefs.edit().putInt("versionCode", BuildConfig.VERSION_CODE).apply()
        }
    }

    override fun onPause() {
        super.onPause()
        // Persist any settings the app writes on pagehide before WebView sleeps.
        webView.evaluateJavascript("window.dispatchEvent(new Event('pagehide'))", null)
        webView.onPause()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onDestroy() {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        captureCallback?.onReceiveValue(null)
        captureCallback = null
        webView.removeJavascriptInterface("NotesNative")
        webView.destroy()
        super.onDestroy()
    }

    private fun notFound(): WebResourceResponse =
        WebResourceResponse(
            "text/plain",
            "utf-8",
            404,
            "Not Found",
            emptyMap(),
            ByteArrayInputStream(ByteArray(0))
        )
}
