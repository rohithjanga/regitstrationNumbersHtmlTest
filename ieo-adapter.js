// Converts reports/ieo-dashboard.json (warehouse export) into the EXTRA.ieo
// shape the dashboard's renderIeoTab() already reads, so the tab works unchanged.
//
// Usage in the page, replacing the embedded EXTRA.ieo:
//   fetch("ieo-dashboard.json").then(r => r.json()).then(report => {
//     EXTRA.ieo = ieoFromReport(report);
//     render();
//   });
//
// Differences from the old embedded data, all deliberate:
//   * North America IEO platform only (the old export counted every region).
//   * Weeks are Monday–Sunday; the old export used Saturday–Friday.
//   * Counts below 5 are merged: a small centre's detail moves into
//     "(other) — <region>", and anything still below 5 is dropped (the amount
//     dropped is report.provenance.suppressedBelowK).
//   * "Active mid-step" is status Active only; Withdrawn and privacy-pooled rows
//     count toward Total but toward neither Active nor Expired.
function ieoFromReport(report) {
  "use strict";
  var STEPS = ["1", "2", "3", "4", "5", "6", "7"];

  // '(other)' exists once per region; give each its own name so the centre
  // filter can tell them apart.
  function centreName(row) {
    return row.centerName === "(other)" ? "(other) — " + row.centerRegion : row.centerName;
  }
  function inc(map, key, field, n) {
    map[key] = map[key] || {};
    map[key][field] = (map[key][field] || 0) + n;
  }
  function pct(a, b) { return b ? Math.round(1000 * a / b) / 10 : 0; }

  // ---- progress: headline, funnel, centres, steps by centre ----
  var byCentre = {}, funnelByCentre = {};
  report.progress.forEach(function (r) {
    var c = centreName(r), n = r.participants;
    inc(byCentre, c, "total", n);
    if (r.status === "Completed") inc(byCentre, c, "completed", n);
    if (r.status === "Expired") inc(byCentre, c, "expired", n);
    if (r.status === "Active") inc(byCentre, c, "active_step", n);
    var m = /^Step (\d)$/.exec(r.stage);
    if (m && r.status !== "Completed") {
      inc(byCentre, c, "s" + m[1], n);                 // everyone not completed, at this step
      var fk = c + "\u0000" + m[1];
      if (r.status === "Expired") inc(funnelByCentre, fk, "expired", n);
      if (r.status === "Active") inc(funnelByCentre, fk, "in_progress", n);
    }
  });

  var centers = [], stepsByCenter = [];
  var tot = { total: 0, completed: 0, expired: 0, active_step: 0 };
  Object.keys(byCentre).forEach(function (c) {
    var v = byCentre[c];
    var row = { center: c, total: v.total || 0, completed: v.completed || 0,
                expired: v.expired || 0, active_step: v.active_step || 0 };
    row.completion_rate = pct(row.completed, row.total);
    centers.push(row);
    var s = { center: c, completed: row.completed, total: row.total };
    STEPS.forEach(function (k) { s["s" + k] = v["s" + k] || 0; });
    stepsByCenter.push(s);
    Object.keys(tot).forEach(function (k) { tot[k] += row[k]; });
  });
  centers.sort(function (a, b) { return b.total - a.total || (a.center < b.center ? -1 : 1); });
  stepsByCenter.sort(function (a, b) { return b.total - a.total || (a.center < b.center ? -1 : 1); });

  var funnelRecords = Object.keys(funnelByCentre).map(function (fk) {
    var parts = fk.split("\u0000"), v = funnelByCentre[fk];
    return { c: parts[0], s: parts[1], expired: v.expired || 0, in_progress: v.in_progress || 0 };
  });
  var funnel = STEPS.map(function (s) {
    var e = 0, a = 0;
    funnelRecords.forEach(function (r) { if (r.s === s) { e += r.expired; a += r.in_progress; } });
    return { step: s, expired: e, in_progress: a };
  });

  // ---- languages ----
  var langTotals = {}, langByCentre = [];
  report.languages.forEach(function (r) {
    langTotals[r.language] = (langTotals[r.language] || 0) + r.participants;
    langByCentre.push({ c: centreName(r), lang: r.language, n: r.participants });
  });
  var langSum = Object.keys(langTotals).reduce(function (a, k) { return a + langTotals[k]; }, 0);
  var languages = Object.keys(langTotals)
    .map(function (l) { return { lang: l, count: langTotals[l], pct: pct(langTotals[l], langSum) }; })
    .sort(function (a, b) { return b.count - a.count || (a.lang < b.lang ? -1 : 1); });

  // ---- completions by month / year ----
  var mb = report.provenance.windowBounds.completionsMonthly;
  var months = [];
  for (var d = new Date(mb.start + "T00:00:00Z"); d.toISOString().slice(0, 10) < mb.end;
       d.setUTCMonth(d.getUTCMonth() + 1)) {
    months.push(d.toISOString().slice(0, 7));
  }
  var monthTotals = {}; months.forEach(function (m) { monthTotals[m] = 0; });
  var monthlyByCenter = report.completionsMonthly.map(function (r) {
    var m = r.month.slice(0, 7);
    monthTotals[m] = (monthTotals[m] || 0) + r.completions;
    return { c: centreName(r), m: m, n: r.completions };
  });
  var yearTotals = {};
  months.forEach(function (m) { yearTotals[m.slice(0, 4)] = (yearTotals[m.slice(0, 4)] || 0) + monthTotals[m]; });
  var yearly = Object.keys(yearTotals).sort().map(function (y) { return { year: y, count: yearTotals[y] }; });
  var last12 = months.slice(-12).map(function (m) { return { month: m, count: monthTotals[m] }; });

  // ---- completions by week (zero-filled from the window: an absent week is a
  //      week with no Sunday batch, not missing data) ----
  var wb = report.provenance.windowBounds.completionsWeekly;
  var weekTotals = {}, weeks = [];
  for (var w = new Date(wb.start + "T00:00:00Z"); w.toISOString().slice(0, 10) < wb.end;
       w.setUTCDate(w.getUTCDate() + 7)) {
    var ws = w.toISOString().slice(0, 10);
    var we = new Date(w.getTime() + 6 * 864e5).toISOString().slice(0, 10);
    weeks.push({ start: ws, end: we }); weekTotals[ws] = 0;
  }
  var weeklyByCenter = report.completionsWeekly.map(function (r) {
    weekTotals[r.weekStart] = (weekTotals[r.weekStart] || 0) + r.completions;
    return { c: centreName(r), w: r.weekStart, n: r.completions };
  });
  var weeklyRecent = weeks.map(function (x) { return { start: x.start, end: x.end, count: weekTotals[x.start] }; });

  function last(a, i) { return a.length > i ? a[a.length - 1 - i].count : 0; }
  return {
    total_records: tot.total,
    completed: tot.completed,
    completion_rate: pct(tot.completed, tot.total),
    total_expired: tot.expired,
    total_active_step: tot.active_step,
    funnel: funnel,
    funnel_by_center: funnelRecords,
    languages: languages,
    languages_by_center: langByCentre,
    centers: centers,
    steps_by_center: stepsByCenter,
    completions_undated: 0,               // every warehouse completion is dated
    completions_latest_date: null,        // not published; use provenance.generatedAt for freshness
    yearly: yearly,
    monthly_last12: last12,
    weekly_recent: weeklyRecent,
    monthly_by_center: monthlyByCenter,
    weekly_by_center: weeklyByCenter,
    this_month: last(last12, 0), prev_month: last(last12, 1), delta_month: last(last12, 0) - last(last12, 1),
    this_week: last(weeklyRecent, 0), prev_week: last(weeklyRecent, 1), delta_week: last(weeklyRecent, 0) - last(weeklyRecent, 1),
    // Extra, for a "data as of / suppressed" note on the tab.
    generated_at: report.provenance.generatedAt,
    suppressed_below_k: report.provenance.suppressedBelowK
  };
}
if (typeof module !== "undefined") module.exports = ieoFromReport;
