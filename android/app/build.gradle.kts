import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.joseph.notes"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.joseph.notes"
        minSdk = 29
        targetSdk = 36
        versionCode = 10000
        versionName = "1.0.0"
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = false
            // Personal sideload build: non-debuggable release, signed with the
            // machine's Android debug key so it installs without a private
            // keystore. Replace with a dedicated signing config before any
            // store or shared-build release.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }

    lint {
        // This app is deliberately one local WebView with a narrow JS bridge.
        disable += "SetJavaScriptEnabled"
        disable += "AddJavascriptInterface"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.androidx.webkit)
}

/**
 * Stage the PWA into the APK at build time. source/ is the one source of truth;
 * nothing under app/src/main/assets/www is edited by hand.
 */
val syncWebAssets by tasks.registering(Copy::class) {
    from(rootProject.file("../source"))
    into(layout.projectDirectory.dir("src/main/assets/www"))
    doFirst {
        delete(layout.projectDirectory.dir("src/main/assets/www"))
    }
}

tasks.named("preBuild") {
    dependsOn(syncWebAssets)
}
