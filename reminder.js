function asDate(value) {
  const date = value instanceof Date ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error("Invalid date");
  }
  return date;
}

function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

function startOfWeekMonday(date) {
  const d = startOfDay(date);
  const day = d.getDay();
  const offset = (day + 6) % 7;
  d.setDate(d.getDate() - offset);
  return d;
}

function daysBetween(a, b) {
  return Math.floor((startOfDay(b) - startOfDay(a)) / 86400000);
}

function monthsBetween(a, b) {
  return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth());
}

function validDateParts(year, month, day) {
  const d = new Date(year, month, day);
  return d.getFullYear() === year && d.getMonth() === month && d.getDate() === day;
}

function withStartTime(date, start) {
  const d = new Date(date);
  d.setHours(start.getHours(), start.getMinutes(), start.getSeconds(), start.getMilliseconds());
  return d;
}

export function normalizeRule(rule = {}) {
  const kind = ["once", "daily", "weekly", "monthly", "yearly"].includes(rule.kind)
    ? rule.kind
    : "once";
  const interval = Math.max(1, Number.parseInt(rule.interval ?? 1, 10) || 1);
  const weekdays = [...new Set((rule.weekdays || [])
    .map(Number)
    .filter(day => Number.isInteger(day) && day >= 0 && day <= 6))]
    .sort((a, b) => a - b);

  return { version: 1, kind, interval, weekdays };
}

export function nextOccurrence(startAt, ruleInput, after = new Date()) {
  const start = asDate(startAt);
  const afterDate = asDate(after);
  const rule = normalizeRule(ruleInput);

  if (rule.kind === "once") {
    return start > afterDate ? start : null;
  }

  if (rule.kind === "daily") {
    if (start > afterDate) return start;
    const elapsedDays = Math.max(0, daysBetween(start, afterDate));
    let n = Math.floor(elapsedDays / rule.interval);
    let candidate = new Date(start);
    candidate.setDate(start.getDate() + n * rule.interval);
    while (candidate <= afterDate) {
      n += 1;
      candidate = new Date(start);
      candidate.setDate(start.getDate() + n * rule.interval);
    }
    return candidate;
  }

  if (rule.kind === "weekly") {
    const weekdays = rule.weekdays.length ? rule.weekdays : [start.getDay()];
    const startWeek = startOfWeekMonday(start);
    let cursor = startOfDay(afterDate > start ? afterDate : start);
    const maxDays = 366 * 12;

    for (let i = 0; i < maxDays; i += 1) {
      const candidateDay = new Date(cursor);
      candidateDay.setDate(cursor.getDate() + i);
      if (!weekdays.includes(candidateDay.getDay())) continue;

      const candidateWeek = startOfWeekMonday(candidateDay);
      const weeksSinceStart = Math.floor((candidateWeek - startWeek) / (7 * 86400000));
      if (weeksSinceStart < 0 || weeksSinceStart % rule.interval !== 0) continue;

      const candidate = withStartTime(candidateDay, start);
      if (candidate >= start && candidate > afterDate) return candidate;
    }
    return null;
  }

  if (rule.kind === "monthly") {
    if (start > afterDate) return start;
    let monthOffset = Math.max(0, monthsBetween(start, afterDate));
    monthOffset -= monthOffset % rule.interval;

    for (let i = 0; i < 2400; i += rule.interval) {
      const offset = monthOffset + i;
      const absoluteMonth = start.getMonth() + offset;
      const year = start.getFullYear() + Math.floor(absoluteMonth / 12);
      const month = ((absoluteMonth % 12) + 12) % 12;
      const day = start.getDate();
      if (!validDateParts(year, month, day)) continue;
      const candidate = new Date(
        year, month, day,
        start.getHours(), start.getMinutes(), start.getSeconds(), start.getMilliseconds()
      );
      if (candidate > afterDate && candidate >= start) return candidate;
    }
    return null;
  }

  if (rule.kind === "yearly") {
    if (start > afterDate) return start;
    let year = Math.max(start.getFullYear(), afterDate.getFullYear());
    const delta = year - start.getFullYear();
    year -= delta % rule.interval;

    for (let i = 0; i < 500; i += rule.interval) {
      const candidateYear = year + i;
      if (!validDateParts(candidateYear, start.getMonth(), start.getDate())) continue;
      const candidate = new Date(
        candidateYear, start.getMonth(), start.getDate(),
        start.getHours(), start.getMinutes(), start.getSeconds(), start.getMilliseconds()
      );
      if (candidate > afterDate && candidate >= start) return candidate;
    }
  }

  return null;
}

export function reminderWithNextDue(reminder, after = new Date()) {
  const next = reminder.enabled === false
    ? null
    : nextOccurrence(reminder.startAt, reminder.recurrence, after);

  return {
    ...reminder,
    nextDueAt: next ? next.toISOString() : null
  };
}
