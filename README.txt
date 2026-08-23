AYAH FRAME STUDIO — LOCAL WEBSITE
==================================
A little Flask website that puts a browser UI in front of the
quran_video.py generator. Pick a surah, reciter, and style in your
browser, click Generate, and it renders the real MP4 for you right
here on your own machine — nothing is uploaded anywhere.

HOW TO RUN
----------
1. Open a terminal in this folder (the one this README is in).

2. Make sure ffmpeg is installed (skip if you already have it):
   - Mac:     brew install ffmpeg
   - Windows: https://ffmpeg.org/download.html (add it to PATH)
   - Linux:   sudo apt install ffmpeg

3. If you haven't already installed the Python packages this project
   needs, run:
   pip install -r requirements.txt

4. Start the website:
   python app.py

5. The terminal will print two addresses, e.g.:
   On this computer -> http://127.0.0.1:5050
   On your phone/other devices (same Wi-Fi) -> http://192.168.1.23:5050

   Open the first one on this computer, or the second one on your
   phone (or any other device) — as long as it's on the same Wi-Fi
   network as this computer. If your phone can't connect:
   - Double-check it's on the same Wi-Fi network, not mobile data.
   - Your computer's firewall may be blocking incoming connections —
     the first time you run this you may get a firewall prompt
     (Mac/Windows) asking to allow Python to accept connections;
     say yes/allow.
   - If it still doesn't work, some public/office Wi-Fi networks block
     devices from seeing each other ("client isolation") — a home
     network usually doesn't have this problem.
   - Want it restricted to just this computer again? Run it as
     HOST=127.0.0.1 python app.py (Mac/Linux) or
     set HOST=127.0.0.1 && python app.py (Windows).

6. In the page: pick a surah, verse range, reciter, translation, and
   orientation, optionally drop in a theme.json exported from the
   Ayah Frame Studio theme editor, then click "Generate video". A live
   log and progress bar will show while it renders — when it's done
   you can preview the video right on the page and download the MP4.

Downloaded audio is cached in a "cache" folder and finished videos are
saved in an "output" folder (both created automatically next to
quran_video.py), so re-generating the same surah later is much faster.

To stop the site, go back to the terminal and press Ctrl+C.

NOTES
-----
- The Ayah Frame Studio theme editor (theme_editor.html) rides along on
  this same server — once "python app.py" is running, open it at
  http://127.0.0.1:5050/theme_editor.html on this computer, or
  http://<the-LAN-address-printed-at-startup>/theme_editor.html on
  your phone or any other device on the same Wi-Fi. There's also a
  link to it at the top of the generator page. Design your theme
  there, hit its Export button to save a theme.json, then drop that
  file into the "Theme" box on the generator page (or pass it to
  quran_video.py with --theme).
- This only runs locally (127.0.0.1) — no one else on your network can
  reach it.
- You still need an internet connection while generating, since the
  Arabic text/translation and recitation audio are fetched live from
  alquran.cloud and everyayah.com, and the surah list in the browser
  is loaded live from alquran.cloud too.
- For a handful of reciters, generating without a manual timing
  manifest plays one continuous, unsplit per-surah recording (via
  quranicaudio.com / mp3quran.net) instead of downloading and gluing
  together one file per ayah, using per-ayah boundary timing data from
  everyayah.com (courtesy of VerseByVerseQuran.com,
  http://versebyversequran.com/site/license). Every other reciter is
  unaffected and keeps downloading per-ayah audio as before.
- You can still use quran_video.py directly from the command line if
  you'd rather script it — see the --help output for every flag
  (--surah, --ayah-start/--ayah-end, --reciter, --translation,
  --orientation, --no-translation, --theme, --output, etc). The
  website is just a friendlier way to call the same script.
