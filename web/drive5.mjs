import { chromium } from "playwright";

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const page = await (await browser.newContext({ viewport: { width: 1400, height: 1200 } })).newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto("http://localhost:5173");
await page.waitForSelector(".event-card", { timeout: 10000 });
await page.screenshot({ path: "/tmp/events-picker.png", fullPage: true });

// Theater layout
await page.click('.event-card:has-text("The Midnight Sessions")');
await page.waitForSelector(".seat", { timeout: 10000 });
await page.screenshot({ path: "/tmp/theater-layout.png", fullPage: true });

await page.click('button:has-text("all events")');
await page.waitForSelector(".event-card");

// Stadium layout
await page.click('.event-card:has-text("Solstice World Tour")');
await page.waitForSelector(".seat", { timeout: 10000 });
await page.screenshot({ path: "/tmp/stadium-layout.png", fullPage: true });

console.log("console errors:", JSON.stringify(errors));
await browser.close();
