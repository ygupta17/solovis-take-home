import { chromium } from "playwright";

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const page = await (await browser.newContext({ viewport: { width: 1400, height: 1200 } })).newPage();
await page.goto("http://localhost:5173");
await page.waitForSelector(".event-card");
await page.click('.event-card:has-text("The Midnight Sessions")');
await page.waitForSelector(".seat");

const info = await page.evaluate(() => {
  const venue = document.querySelector(".venue");
  const scroll = document.querySelector(".venue-scroll");
  const seats = [...document.querySelectorAll(".seat")];
  const rects = seats.map((s) => s.getBoundingClientRect());
  const maxRight = Math.max(...rects.map((r) => r.right));
  const minLeft = Math.min(...rects.map((r) => r.left));
  return {
    venueStyleWidth: venue.style.width,
    venueStyleHeight: venue.style.height,
    venueRect: venue.getBoundingClientRect(),
    scrollRect: scroll.getBoundingClientRect(),
    seatCount: seats.length,
    maxRight,
    minLeft,
  };
});
console.log(JSON.stringify(info, null, 2));
await browser.close();
