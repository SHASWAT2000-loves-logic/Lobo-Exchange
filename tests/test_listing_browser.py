"""Exercise the real listing page script in a browser with mocked AI responses."""

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import create_app, db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHROME = next((path for path in (
    shutil.which("chrome"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
) if path and Path(path).is_file()), None)


@unittest.skipUnless(CHROME, "Chrome or Edge is needed for the browser script check")
class ListingBrowserTest(unittest.TestCase):
    def run_browser_check(self, check_script):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "browser-test-only",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "UPLOAD_FOLDER": str(work / "uploads"),
            })
            client = app.test_client()
            register_page = client.get("/register").data
            csrf = re.search(rb'name="csrf_token" value="([a-f0-9]+)"', register_page).group(1).decode()
            client.post("/register", data={
                "csrf_token": csrf, "email": "browser@unm.edu", "password": "password123",
            })
            page = client.get("/listings/new").data.decode()
            page = re.sub(r'<link[^>]+(?:bootstrap|style\.css)[^>]*>', '', page)
            page = re.sub(
                r'/static/listing_ai\.js\?v=\d+',
                (PROJECT_ROOT / "public" / "static" / "listing_ai.js").as_uri(),
                page,
            )
            page = page.replace("</body>", '<pre id="results"></pre><script>' + check_script + '</script></body>')
            fixture = work / "listing.html"
            fixture.write_text(page, encoding="utf-8")

            process = subprocess.run([
                CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--allow-file-access-from-files", f"--user-data-dir={work / 'profile'}",
                "--virtual-time-budget=5000", "--dump-dom", fixture.as_uri(),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
            match = re.search(r'<pre id="results">(.*?)</pre>', process.stdout, re.S)
            self.assertIsNotNone(match, process.stderr[-1000:] + process.stdout[-1000:])
            result = json.loads(html.unescape(match.group(1)))
            self.assertTrue(result["passed"], result)
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()

    def test_generate_edits_review_warnings_and_publish(self):
        self.run_browser_check(BROWSER_CHECK)

    def test_generate_preserves_populated_fields_until_suggestion_is_accepted(self):
        self.run_browser_check(PREFILLED_CHECK)


BROWSER_CHECK = r"""
let pendingGenerate;
const reviewRequests = [];
window.fetch = (url, options) => {
  if (url.includes('/listings/suggest')) {
    return new Promise(resolve => { pendingGenerate = resolve; });
  }
  if (url.includes('/listings/analyze')) {
    const fields = Object.fromEntries(options.body.entries());
    reviewRequests.push(fields);
    const warnings = [];
    if (fields.description.includes('505-555-0123')) warnings.push('Your listing may include a phone number.');
    if (fields.description.includes('Need payment immediately')) warnings.push('Some wording may warrant extra caution.');
    if (Number(fields.price) > Number(fields.original_estimated_price_high) * 1.5) {
      warnings.push('Your asking price is far outside the earlier AI estimate.');
    }
    return Promise.resolve({ok: true, json: async () => ({
      warnings, reasons: warnings.map(() => 'Review before publishing.'), ai_unavailable: false,
    })});
  }
  throw Error('Unexpected fetch: ' + url);
};

window.addEventListener('DOMContentLoaded', async () => {
  const form = document.getElementById('listing-form');
  const field = name => form.elements[name];
  const panel = document.getElementById('safety-review');
  const report = document.getElementById('results');
  const pause = () => new Promise(resolve => setTimeout(resolve, 20));
  const check = (condition, message) => { if (!condition) throw Error(message); };
  let finalSubmits = 0;
  form.addEventListener('submit', event => {
    if (!event.defaultPrevented) {
      event.preventDefault();
      finalSubmits += 1;
    }
  });
  try {
    const files = new DataTransfer();
    files.items.add(new File(['ordinary image bytes'], 'mattress.png', {type: 'image/png'}));
    field('image').files = files.files;
    field('image').dispatchEvent(new Event('change', {bubbles: true}));

    document.getElementById('generate-ai').click();
    check(!!pendingGenerate, 'Generate did not request suggestions');
    pendingGenerate({ok: true, json: async () => ({
      title: 'Mattress', description: 'Used mattress.', category: 'Furniture',
      estimated_price_low: '300.00', estimated_price_high: '400.00',
    })});
    await pause();
    check(field('title').value === 'Mattress', 'Generate did not fill title');
    check(field('description').value === 'Used mattress.', 'Generate did not fill description');
    check(field('category').value === 'Furniture', 'Generate did not fill category');
    check(field('price').value === '350.00', 'Generate did not fill asking price');
    check(!document.getElementById('ai-ready-banner').hidden, 'AI-ready banner is hidden');
    check(document.getElementById('ai-price-range').textContent.includes('$300.00–$400.00'), 'Price range guidance is missing');

    field('price').value = '1000.00';
    field('price').dispatchEvent(new Event('input', {bubbles: true}));
    const editedDescription = 'Call me at 505-555-0123 if interested. Need payment immediately. Zelle only. Pay before pickup.';
    field('description').value = editedDescription;
    field('description').dispatchEvent(new Event('input', {bubbles: true}));
    form.requestSubmit();
    await pause();
    check(field('price').value === '1000.00', 'Review changed the edited price');
    check(field('description').value === editedDescription, 'Review changed the edited description');
    check(reviewRequests.length === 1, 'Review did not send current form values');
    check(reviewRequests[0].price === '1000.00', 'Review sent the wrong price');
    check(reviewRequests[0].description === editedDescription, 'Review sent the wrong description');
    check(reviewRequests[0].original_estimated_price_high === '400.00', 'Original AI estimate was lost');
    check(!panel.hidden, 'Review panel is hidden');
    const displayed = document.getElementById('safety-warnings').textContent;
    check(displayed.includes('phone number'), 'Privacy warning is not visible');
    check(displayed.includes('caution'), 'Suspicious wording warning is not visible');
    check(displayed.includes('asking price'), 'Unusual price warning is not visible');
    check(finalSubmits === 0, 'Review published automatically');

    // A Generate response arriving after edits and Review must be ignored.
    document.getElementById('generate-ai').click();
    check(!!pendingGenerate, 'Second Generate did not start');
    field('price').value = '1200.00';
    field('price').dispatchEvent(new Event('input', {bubbles: true}));
    field('description').value = 'Final edited description.';
    field('description').dispatchEvent(new Event('input', {bubbles: true}));
    form.requestSubmit();
    await pause();
    pendingGenerate({ok: true, json: async () => ({
      title: 'Wrong late title', description: 'Wrong late description', category: 'Other',
      estimated_price_low: '1.00', estimated_price_high: '2.00',
    })});
    await pause();
    check(field('title').value === 'Mattress', 'Late Generate changed title');
    check(field('description').value === 'Final edited description.', 'Late Generate changed description');
    check(field('price').value === '1200.00', 'Late Generate changed price');
    check(field('original_estimated_price_high').value === '400.00', 'Late Generate changed saved guidance');
    check(!panel.hidden, 'Second Review panel is hidden');
    document.getElementById('safety-publish').click();
    check(finalSubmits === 1, 'Publish anyway did not explicitly submit');
    report.textContent = JSON.stringify({passed: true});
  } catch (error) {
    report.textContent = JSON.stringify({passed: false, error: String(error)});
  }
});
"""


PREFILLED_CHECK = r"""
let pendingGenerate;
const generateRequests = [];
window.fetch = (url, options) => {
  if (!url.includes('/listings/suggest')) throw Error('Unexpected fetch: ' + url);
  generateRequests.push(Object.fromEntries(options.body.entries()));
  return new Promise(resolve => { pendingGenerate = resolve; });
};

window.addEventListener('DOMContentLoaded', async () => {
  const form = document.getElementById('listing-form');
  const field = name => form.elements[name];
  const report = document.getElementById('results');
  const pause = () => new Promise(resolve => setTimeout(resolve, 20));
  const check = (condition, message) => { if (!condition) throw Error(message); };
  const suggestion = name => document.getElementById('ai-suggestion-' + name);
  const respond = (title, description, category, low, high) => pendingGenerate({
    ok: true, json: async () => ({
      title, description, category, estimated_price_low: low, estimated_price_high: high,
    }),
  });
  try {
    const files = new DataTransfer();
    files.items.add(new File(['ordinary image bytes'], 'chair.png', {type: 'image/png'}));
    field('image').files = files.files;
    field('image').dispatchEvent(new Event('change', {bubbles: true}));

    field('title').value = 'My chair title';
    field('price').value = '1000.00';
    document.getElementById('generate-ai').click();
    check(generateRequests[0].title === 'My chair title', 'Seller title was not sent to AI');
    check(generateRequests[0].price === '1000.00', 'Seller asking price was not sent to AI');
    respond('AI chair title', 'AI chair description', 'Furniture', '80.00', '120.00');
    await pause();
    check(field('title').value === 'My chair title', 'Generate overwrote a populated title');
    check(field('price').value === '1000.00', 'Generate overwrote a populated price');
    check(field('description').value === 'AI chair description', 'Generate did not fill empty description');
    check(field('category').value === 'Furniture', 'Generate did not fill empty category');
    check(field('estimated_price_low').value === '80.00', 'Generate did not fill empty low estimate');
    check(field('estimated_price_high').value === '120.00', 'Generate did not fill empty high estimate');
    check(suggestion('title') && !suggestion('title').hidden, 'Title suggestion is not shown separately');
    check(suggestion('price') && !suggestion('price').hidden, 'Price suggestion is not shown separately');
    check(suggestion('price').textContent.includes('$80.00–$120.00'), 'Suggested price range is missing');
    check(field('original_estimated_price_high').value === '120.00', 'Original AI guidance was not saved');

    field('description').value = 'My own description';
    field('category').value = 'Other';
    field('estimated_price_low').value = '25.00';
    field('estimated_price_high').value = '50.00';
    document.getElementById('generate-ai').click();
    check(generateRequests[1].description === 'My own description', 'Seller description was not sent to AI');
    check(generateRequests[1].category === 'Other', 'Seller category was not sent to AI');
    respond('Second AI title', 'Second AI description', 'Books', '200.00', '300.00');
    await pause();
    const expected = {
      title: 'My chair title', description: 'My own description', category: 'Other',
      price: '1000.00', estimated_price_low: '25.00', estimated_price_high: '50.00',
    };
    for (const [name, value] of Object.entries(expected)) {
      check(field(name).value === value, 'Generate overwrote populated ' + name);
      check(suggestion(name) && !suggestion(name).hidden, 'No separate AI suggestion for ' + name);
      check(suggestion(name).querySelector('button').textContent === 'Use', 'No explicit use button for ' + name);
      check(suggestion(name).querySelectorAll('button')[1].textContent === 'Dismiss', 'No dismiss button for ' + name);
      check(!suggestion(name).className.includes('border'), 'Suggestion is still a large card for ' + name);
      check(suggestion(name).querySelector('button').className.includes('outline-secondary'), 'Use button has destructive styling');
    }
    check(suggestion('title').textContent.includes('Second AI title'), 'Newest title suggestion is missing');
    check(field('original_estimated_price_low').value === '200.00', 'Newest original AI estimate was not saved');
    check(suggestion('price').textContent.includes('Suggested range: $200.00–$300.00'), 'Price suggestion did not show a range');
    check(document.getElementById('ai-price-range').textContent.includes('$200.00–$300.00'), 'Price guidance beside asking price is missing');

    suggestion('title').querySelectorAll('button')[1].click();
    check(field('title').value === 'My chair title', 'Dismissing changed the seller title');
    check(suggestion('title').hidden, 'Dismissed suggestion is still visible');

    suggestion('price').querySelector('button').click();
    check(field('price').value === '250.00', 'Explicit button did not apply suggested price');
    check(field('title').value === 'My chair title', 'Accepting price changed title');
    check(suggestion('price').hidden, 'Accepted suggestion was not dismissed');
    suggestion('description').querySelector('button').click();
    check(field('description').value === 'Second AI description', 'Explicit button did not apply suggested description');

    document.getElementById('generate-ai').click();
    field('price').value = '1200.00';
    field('price').dispatchEvent(new Event('input', {bubbles: true}));
    respond('MY CHAIR TITLE.', 'Second AI description.', 'Other', '20.00', '30.00');
    await pause();
    check(field('price').value === '1200.00', 'Late Generate overwrote a price typed during the request');
    check(suggestion('price').textContent.includes('$20.00–$30.00'), 'Late Generate did not offer price range separately');
    check(suggestion('title').hidden, 'Redundant title suggestion appeared');
    check(suggestion('description').hidden, 'Redundant description suggestion appeared');
    check(suggestion('category').hidden, 'Redundant category suggestion appeared');
    report.textContent = JSON.stringify({passed: true});
  } catch (error) {
    report.textContent = JSON.stringify({passed: false, error: String(error)});
  }
});
"""


if __name__ == "__main__":
    unittest.main()
