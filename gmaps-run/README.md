# Lead Finder

Search Google Maps for businesses (for example **hotels in Jaipur**) and see every result in a
dashboard: name, phone, email, website, full address, opening hours, ratings, reviews, photos
and a map. Results can be downloaded as CSV.

It runs on your own computer. It uses the open-source
[google-maps-scraper](https://github.com/gosom/google-maps-scraper) (MIT licence), which the
setup downloads for you.

---

## Set up on a new computer (Windows)

You need about 1 GB of free space and an internet connection.

1. **Install Python 3** from <https://www.python.org/downloads/>.
   On the first installer screen, tick **"Add python.exe to PATH"**.
2. **Download it:**
   - With Git: `git clone https://github.com/BhavyaJain00/Google-.git`
   - Without Git: open <https://github.com/BhavyaJain00/Google-> and click **Code → Download ZIP**, then unzip it.
3. Open the **`gmaps-run`** folder inside it and double-click **`Setup (run once).bat`**.
   It downloads the scraper (checked against its official checksum) and the browser it uses.
4. Double-click **`Open Lead Finder.bat`**. The dashboard opens at <http://localhost:8080>.

Next time, only do step 4.

## Set up on a Mac or Linux computer

```bash
git clone https://github.com/BhavyaJain00/Google-.git
cd Google-/gmaps-run
chmod +x setup.sh && ./setup.sh     # one time
./start.sh                          # every time; stop with Ctrl+C
```

Then open <http://localhost:8080>.

---

## Using it

1. Type **what in where**, for example `paper cup manufacturers in Jaipur`, and press **Search**.
2. **Coverage**
   - **Whole area** (recommended): Google shows at most about 120 places per search, and
     sometimes only about 20. Whole area splits the city into map zones, searches each zone
     and merges the results without duplicates. The line under the search box shows which
     place was found and how many zones will be searched. Check it, because names like
     "Amer" or "Sanganer" exist in several places. Add the city (`Vaishali Nagar, Jaipur`)
     to be precise.
   - **Exact search only**: one quick search, exactly as typed.
3. **Find emails** visits each business website and collects emails written on its home page.
   Google Maps itself does not show emails, so chains and government offices often have none.
4. Click any place to see all its details. Use the filters, sort and **Table / Cards / Map**
   views, and **Download CSV** to export.

## Open it from a phone or another computer

While it runs, other devices on the **same Wi-Fi/network** can open
`http://<this-computer's-IP>:8080`. The dashboard window shows the exact address when it starts.

- The first time, Windows may ask whether Python can use the network. Click **Allow access**
  for private networks.
- There is no password. Anyone on the same network can open it, so use it only on networks
  you trust.

## Where results are saved

Results are saved in the `webdata` folder on each computer. They are **not** uploaded to
GitHub. To move old results to a new computer, copy the `webdata` folder across while Lead
Finder is closed.

## Good to know

- Always start it with **`Open Lead Finder.bat`** (or `./start.sh`). If you run
  `google-maps-scraper.exe` directly, it takes port 8080 and the dashboard cannot start.
- Big areas take time. A whole city can take 30 minutes to a few hours, and places appear on
  screen as they are found. The search stops by itself when it finishes.
- To find the area, the city name you type is sent to OpenStreetMap's free place search
  (nominatim.openstreetmap.org). Only the place name is sent.
- Scraping Google Maps may be against Google's terms of service. Many requests in a short
  time can get temporarily blocked. If you email the contacts you find, follow anti-spam and
  privacy laws.

## Update to the latest version

In the folder, run `git pull`, or download the ZIP again. Your `webdata` folder is kept.

## Files

| File | What it is |
|---|---|
| `Open Lead Finder.bat` / `start.sh` | Start the dashboard (it also starts the scraper) |
| `Setup (run once).bat` / `setup.ps1` / `setup.sh` | One-time setup |
| `dashboard/server.py` | Dashboard server (Python, no extra packages needed) |
| `dashboard/index.html` | Dashboard page |
| `dashboard/vendor/` | Leaflet map library (BSD-2 licence) |
| `webdata/` | Your results (created on first run, not uploaded) |
