/* ============================================================
   Ayah Frame Studio -- shared static reference data
   Surah metadata (number, Arabic name, English name, ayah count)
   and the 4 built-in theme presets shown as swatches on the
   "New video" screen. Theme field names mirror quran_lib/theme.py's
   DEFAULT_THEME exactly -- see frame_editor.html's themeToState()/
   stateToTheme() for the mapping between this on-disk shape and the
   editor's in-memory `state`.
   ============================================================ */

window.SURAHS = [
  {"n":1,"ar":"الفاتحة","en":"Al-Fatihah","ayahs":7},{"n":2,"ar":"البقرة","en":"Al-Baqarah","ayahs":286},
  {"n":3,"ar":"آل عمران","en":"Aal-E-Imran","ayahs":200},{"n":4,"ar":"النساء","en":"An-Nisa","ayahs":176},
  {"n":5,"ar":"المائدة","en":"Al-Ma'idah","ayahs":120},{"n":6,"ar":"الأنعام","en":"Al-An'am","ayahs":165},
  {"n":7,"ar":"الأعراف","en":"Al-A'raf","ayahs":206},{"n":8,"ar":"الأنفال","en":"Al-Anfal","ayahs":75},
  {"n":9,"ar":"التوبة","en":"At-Tawbah","ayahs":129},{"n":10,"ar":"يونس","en":"Yunus","ayahs":109},
  {"n":11,"ar":"هود","en":"Hud","ayahs":123},{"n":12,"ar":"يوسف","en":"Yusuf","ayahs":111},
  {"n":13,"ar":"الرعد","en":"Ar-Ra'd","ayahs":43},{"n":14,"ar":"إبراهيم","en":"Ibrahim","ayahs":52},
  {"n":15,"ar":"الحجر","en":"Al-Hijr","ayahs":99},{"n":16,"ar":"النحل","en":"An-Nahl","ayahs":128},
  {"n":17,"ar":"الإسراء","en":"Al-Isra","ayahs":111},{"n":18,"ar":"الكهف","en":"Al-Kahf","ayahs":110},
  {"n":19,"ar":"مريم","en":"Maryam","ayahs":98},{"n":20,"ar":"طه","en":"Ta-Ha","ayahs":135},
  {"n":21,"ar":"الأنبياء","en":"Al-Anbiya","ayahs":112},{"n":22,"ar":"الحج","en":"Al-Hajj","ayahs":78},
  {"n":23,"ar":"المؤمنون","en":"Al-Mu'minun","ayahs":118},{"n":24,"ar":"النور","en":"An-Nur","ayahs":64},
  {"n":25,"ar":"الفرقان","en":"Al-Furqan","ayahs":77},{"n":26,"ar":"الشعراء","en":"Ash-Shu'ara","ayahs":227},
  {"n":27,"ar":"النمل","en":"An-Naml","ayahs":93},{"n":28,"ar":"القصص","en":"Al-Qasas","ayahs":88},
  {"n":29,"ar":"العنكبوت","en":"Al-Ankabut","ayahs":69},{"n":30,"ar":"الروم","en":"Ar-Rum","ayahs":60},
  {"n":31,"ar":"لقمان","en":"Luqman","ayahs":34},{"n":32,"ar":"السجدة","en":"As-Sajdah","ayahs":30},
  {"n":33,"ar":"الأحزاب","en":"Al-Ahzab","ayahs":73},{"n":34,"ar":"سبأ","en":"Saba","ayahs":54},
  {"n":35,"ar":"فاطر","en":"Fatir","ayahs":45},{"n":36,"ar":"يس","en":"Ya-Sin","ayahs":83},
  {"n":37,"ar":"الصافات","en":"As-Saffat","ayahs":182},{"n":38,"ar":"ص","en":"Sad","ayahs":88},
  {"n":39,"ar":"الزمر","en":"Az-Zumar","ayahs":75},{"n":40,"ar":"غافر","en":"Ghafir","ayahs":85},
  {"n":41,"ar":"فصلت","en":"Fussilat","ayahs":54},{"n":42,"ar":"الشورى","en":"Ash-Shuraa","ayahs":53},
  {"n":43,"ar":"الزخرف","en":"Az-Zukhruf","ayahs":89},{"n":44,"ar":"الدخان","en":"Ad-Dukhan","ayahs":59},
  {"n":45,"ar":"الجاثية","en":"Al-Jathiyah","ayahs":37},{"n":46,"ar":"الأحقاف","en":"Al-Ahqaf","ayahs":35},
  {"n":47,"ar":"محمد","en":"Muhammad","ayahs":38},{"n":48,"ar":"الفتح","en":"Al-Fath","ayahs":29},
  {"n":49,"ar":"الحجرات","en":"Al-Hujurat","ayahs":18},{"n":50,"ar":"ق","en":"Qaf","ayahs":45},
  {"n":51,"ar":"الذاريات","en":"Adh-Dhariyat","ayahs":60},{"n":52,"ar":"الطور","en":"At-Tur","ayahs":49},
  {"n":53,"ar":"النجم","en":"An-Najm","ayahs":62},{"n":54,"ar":"القمر","en":"Al-Qamar","ayahs":55},
  {"n":55,"ar":"الرحمن","en":"Ar-Rahman","ayahs":78},{"n":56,"ar":"الواقعة","en":"Al-Waqi'ah","ayahs":96},
  {"n":57,"ar":"الحديد","en":"Al-Hadid","ayahs":29},{"n":58,"ar":"المجادلة","en":"Al-Mujadilah","ayahs":22},
  {"n":59,"ar":"الحشر","en":"Al-Hashr","ayahs":24},{"n":60,"ar":"الممتحنة","en":"Al-Mumtahanah","ayahs":13},
  {"n":61,"ar":"الصف","en":"As-Saff","ayahs":14},{"n":62,"ar":"الجمعة","en":"Al-Jumu'ah","ayahs":11},
  {"n":63,"ar":"المنافقون","en":"Al-Munafiqun","ayahs":11},{"n":64,"ar":"التغابن","en":"At-Taghabun","ayahs":18},
  {"n":65,"ar":"الطلاق","en":"At-Talaq","ayahs":12},{"n":66,"ar":"التحريم","en":"At-Tahrim","ayahs":12},
  {"n":67,"ar":"الملك","en":"Al-Mulk","ayahs":30},{"n":68,"ar":"القلم","en":"Al-Qalam","ayahs":52},
  {"n":69,"ar":"الحاقة","en":"Al-Haqqah","ayahs":52},{"n":70,"ar":"المعارج","en":"Al-Ma'arij","ayahs":44},
  {"n":71,"ar":"نوح","en":"Nuh","ayahs":28},{"n":72,"ar":"الجن","en":"Al-Jinn","ayahs":28},
  {"n":73,"ar":"المزمل","en":"Al-Muzzammil","ayahs":20},{"n":74,"ar":"المدثر","en":"Al-Muddaththir","ayahs":56},
  {"n":75,"ar":"القيامة","en":"Al-Qiyamah","ayahs":40},{"n":76,"ar":"الإنسان","en":"Al-Insan","ayahs":31},
  {"n":77,"ar":"المرسلات","en":"Al-Mursalat","ayahs":50},{"n":78,"ar":"النبأ","en":"An-Naba","ayahs":40},
  {"n":79,"ar":"النازعات","en":"An-Nazi'at","ayahs":46},{"n":80,"ar":"عبس","en":"Abasa","ayahs":42},
  {"n":81,"ar":"التكوير","en":"At-Takwir","ayahs":29},{"n":82,"ar":"الإنفطار","en":"Al-Infitar","ayahs":19},
  {"n":83,"ar":"المطففين","en":"Al-Mutaffifin","ayahs":36},{"n":84,"ar":"الإنشقاق","en":"Al-Inshiqaq","ayahs":25},
  {"n":85,"ar":"البروج","en":"Al-Buruj","ayahs":22},{"n":86,"ar":"الطارق","en":"At-Tariq","ayahs":17},
  {"n":87,"ar":"الأعلى","en":"Al-A'la","ayahs":19},{"n":88,"ar":"الغاشية","en":"Al-Ghashiyah","ayahs":26},
  {"n":89,"ar":"الفجر","en":"Al-Fajr","ayahs":30},{"n":90,"ar":"البلد","en":"Al-Balad","ayahs":20},
  {"n":91,"ar":"الشمس","en":"Ash-Shams","ayahs":15},{"n":92,"ar":"الليل","en":"Al-Layl","ayahs":21},
  {"n":93,"ar":"الضحى","en":"Ad-Duha","ayahs":11},{"n":94,"ar":"الشرح","en":"Ash-Sharh","ayahs":8},
  {"n":95,"ar":"التين","en":"At-Tin","ayahs":8},{"n":96,"ar":"العلق","en":"Al-Alaq","ayahs":19},
  {"n":97,"ar":"القدر","en":"Al-Qadr","ayahs":5},{"n":98,"ar":"البينة","en":"Al-Bayyinah","ayahs":8},
  {"n":99,"ar":"الزلزلة","en":"Az-Zalzalah","ayahs":8},{"n":100,"ar":"العاديات","en":"Al-Adiyat","ayahs":11},
  {"n":101,"ar":"القارعة","en":"Al-Qari'ah","ayahs":11},{"n":102,"ar":"التكاثر","en":"At-Takathur","ayahs":8},
  {"n":103,"ar":"العصر","en":"Al-Asr","ayahs":3},{"n":104,"ar":"الهمزة","en":"Al-Humazah","ayahs":9},
  {"n":105,"ar":"الفيل","en":"Al-Fil","ayahs":5},{"n":106,"ar":"قريش","en":"Quraysh","ayahs":4},
  {"n":107,"ar":"الماعون","en":"Al-Ma'un","ayahs":7},{"n":108,"ar":"الكوثر","en":"Al-Kawthar","ayahs":3},
  {"n":109,"ar":"الكافرون","en":"Al-Kafirun","ayahs":6},{"n":110,"ar":"النصر","en":"An-Nasr","ayahs":3},
  {"n":111,"ar":"المسد","en":"Al-Masad","ayahs":5},{"n":112,"ar":"الإخلاص","en":"Al-Ikhlas","ayahs":4},
  {"n":113,"ar":"الفلق","en":"Al-Falaq","ayahs":5},{"n":114,"ar":"الناس","en":"An-Nas","ayahs":6}
];

window.DEFAULT_THEME = () => ({
  background_style: "gradient",
  bg_top: [10, 20, 35], bg_bottom: [25, 45, 70], bg_solid: [10, 20, 35],
  arabic_color: [240, 240, 235], translation_color: [200, 205, 210],
  header_color: [196, 164, 96], badge_color: [196, 164, 96],
  arabic_font_regular: "fonts/NotoNaskhArabic-Regular.ttf",
  arabic_font_bold: "fonts/NotoNaskhArabic-Bold.ttf",
  latin_font: "fonts/NotoSans-Regular.ttf",
  margin_frac: 0.09, header_y_frac: 0.06, header_size_frac: 0.028,
  arabic_center_y_frac: 0.36, arabic_size_max_frac: 0.052, arabic_size_min_frac: 0.03,
  arabic_line_height_mult: 1.55, translation_gap_frac: 0.05, translation_size_frac: 0.024,
  translation_position: "below", text_align: "center",
  show_header: true, show_badge: true, show_translation: true, badge_size_frac: 0.022,
  background_image: null, background_video: null, background_overlay_opacity: 0.45,
  fade_duration: 0.6, ken_burns: false,
  highlight_enabled: false, highlight_style: "pill", highlight_color: [255, 197, 87],
  highlight_bg_opacity: 0.85, highlight_fallback_wps: 2.2,
  highlight_pointer_enabled: false, highlight_pointer_style: "hand", highlight_pointer_gap_mult: 1.0,
});

// The 4 built-in swatches on the "New video" screen. Colors match each
// swatch's CSS gradient in the design 1:1; foreground colors picked for
// legibility against that background.
window.THEME_PRESETS = {
  "Sunset Amber": {
    ...window.DEFAULT_THEME(),
    bg_top: [252, 235, 199], bg_bottom: [232, 184, 95],
    arabic_color: [58, 38, 10], header_color: [90, 58, 15], badge_color: [90, 58, 15],
    translation_color: [96, 68, 30],
  },
  "Midnight Teal": {
    ...window.DEFAULT_THEME(),
    bg_top: [30, 58, 58], bg_bottom: [13, 35, 35],
    arabic_color: [217, 198, 138], header_color: [217, 198, 138], badge_color: [217, 198, 138],
    translation_color: [180, 195, 190],
  },
  "Ivory Minimal": {
    ...window.DEFAULT_THEME(),
    bg_top: [255, 253, 246], bg_bottom: [236, 227, 204],
    arabic_color: [20, 24, 58], header_color: [20, 24, 58], badge_color: [173, 130, 38],
    translation_color: [108, 112, 148],
  },
  "Rose Dusk": {
    ...window.DEFAULT_THEME(),
    bg_top: [58, 30, 40], bg_bottom: [22, 11, 16],
    arabic_color: [226, 169, 160], header_color: [226, 169, 160], badge_color: [226, 169, 160],
    translation_color: [200, 160, 160],
  },
};
