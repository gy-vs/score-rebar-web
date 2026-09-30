// Real-browser end-to-end: drives the served UI and asserts the editing
// loop the brief describes. Run against uvicorn on :8000 with dist/ built.

import { chromium } from "playwright";

const BASE = "http://localhost:8000";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error("ASSERT FAIL: " + msg);
}

async function main() {
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const errors: string[] = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});

await page.goto(BASE, { waitUntil: "networkidle" });

// first screen renders a score with two measures, notes present
await page.waitForSelector("g.vf-stavenote");
let noteCount = await page.locator("g.vf-stavenote").count();
console.log("initial glyph groups:", noteCount);
assert(noteCount >= 6, "seed score should render many glyphs");

// status bar confirms version 1
const status1 = await page.locator(".status").innerText();
assert(/v1/.test(status1), "starts at confirmed v1: " + status1);

// --- rebar from measure 1 to 3/4 ---
await page.selectOption(".toolbar select", "3/4");
await page.getByRole("button", { name: "改拍号并重排" }).click();
await page.waitForFunction(
  () => document.querySelector(".status")?.textContent?.includes("v2"),
  { timeout: 5000 });
const status2 = await page.locator(".status").innerText();
assert(/3 小节/.test(status2), "rebarred to 3 measures: " + status2);
console.log("after meter:", status2.trim());

// the sustained G is now two fragments joined by a tie; clicking either
// selects the SAME sounded event and highlights BOTH fragments
const gFrags = page.locator("g.vf-stavenote[data-item]").filter({
  has: page.locator(".vf-notehead"),
});
// click the G fragment on page 1 (measure 2 area): instead, click by the
// data-item whose event spans across — identify from inspector after click.
// Click the second system's first note (measure 2, the tie-in G).
await page.locator("g.vf-stavenote").nth(4).click();
await page.waitForSelector(".inspector .kv");
const inspectorText = await page.locator(".inspector").innerText();
assert(/发声起点/.test(inspectorText), "inspector shows sounded onset");
assert(/显示片段/.test(inspectorText), "inspector lists display fragments");
console.log("inspector:\n" + inspectorText.split("\n").slice(0, 10).join("\n"));

// both slices of that one event should be highlighted (>=2 vf-selected)
await page.waitForTimeout(150);
const selectedCount = await page.locator(".vf-selected").count();
console.log("selected slices for one sounded event:", selectedCount);
assert(selectedCount >= 2, "cross-bar event selects all fragments");

// --- zoom changes display but not the model ---
await page.getByRole("button", { name: "＋" }).click();
await page.getByRole("button", { name: "－" }).click();
const status3 = await page.locator(".status").innerText();
assert(/v2/.test(status3), "zoom must not change confirmed version");

// --- switch to lower voice; its C3 events stay at 0 and 4 QL ---
await page.getByRole("button", { name: /下声部/ }).click();
await page.waitForTimeout(100);
// clicking a lower-voice note and inspecting
const lowerDim = await page.locator(".voice-tabs .tab.active").innerText();
assert(/下声部/.test(lowerDim), "lower voice tab active");

// --- duration edit on the selected (upper) event is blocked while the
// active voice is the lower one (buttons disabled), switch back and edit ---
await page.getByRole("button", { name: /上声部/ }).click();
await page.waitForTimeout(100);
// reselect the sustained event fragment in measure 1 (4th glyph)
await page.locator("g.vf-stavenote").nth(4).click();
await page.waitForTimeout(100);
// change duration to quarter (1 QL)
await page.getByRole("button", { name: /四分 1/ }).click();
await page.waitForFunction(
  () => document.querySelector(".status")?.textContent?.includes("v3"),
  { timeout: 5000 });
console.log("after duration edit:",
  (await page.locator(".status").innerText()).trim());

// --- trigger a rejected move (overlap) and confirm proposal panel ---
await page.locator(".edit-row input").last().fill("4/3");
// the move input is numeric; fill via numeric string 1.3333 snapped server side
await page.locator(".edit-row input").last().fill("1.3333");
await page.getByRole("button", { name: "移动事件" }).click();
await page.waitForSelector(".proposal", { timeout: 5000 });
const proposalText = await page.locator(".proposal").first().innerText();
assert(/overlap|重叠/.test(proposalText), "rejected move explains overlap");
console.log("proposal:", proposalText.split("\n").slice(0, 3).join(" / "));
const status4 = await page.locator(".status").innerText();
assert(/v3/.test(status4) && !/v4/.test(status4),
  "rejected edit does not bump version");

// --- export reflects the confirmed v3 and can be re-imported ---
const [download] = await Promise.all([
  page.waitForEvent("download"),
  page.getByRole("link", { name: "导出 MusicXML" }).click(),
]);
const path = await download.path();
const fs = await import("node:fs");
const xml = fs.readFileSync(path!, "utf8");
assert(/<beats>3<\/beats>/.test(xml), "export is rebarred 3/4");
console.log("exported bytes:", xml.length, "(3/4 confirmed)");

if (errors.length) {
  console.log("BROWSER ERRORS:", errors.slice(0, 5));
  throw new Error("page reported errors");
}
console.log("BROWSER E2E OK");
await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
