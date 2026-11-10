"""Every user-facing string, English and Arabic. `t("key", name=...)` from any thread.

Add a language by adding a column; add a string by adding a row. A missing key shows the
key itself, so gaps are visible instead of crashing.
"""

from __future__ import annotations

LANGUAGES = {"en": "English", "ar": "العربية"}
RTL = {"ar"}
_lang = "en"

# key: (English, Arabic)
STRINGS: dict[str, tuple[str, str]] = {
    # tray status
    "status.idle": ("Waiting for a game", "في انتظار لعبة"),
    "status.wait_obs": ("{game}: waiting for OBS", "{game}: في انتظار OBS"),
    "status.exit_debounce": ("{game}: closing", "{game}: جارٍ الإغلاق"),
    "status.session": ("{game}: recording", "{game}: جارٍ التسجيل"),
    "status.hybrid": ("{game}: recording", "{game}: جارٍ التسجيل"),
    "status.events": ("{game}: ready to clip", "{game}: جاهز لحفظ اللقطات"),
    "status.match_wait": ("{game}: waiting for a match", "{game}: في انتظار مباراة"),
    "status.match_rec": ("{game}: recording match", "{game}: جارٍ تسجيل المباراة"),
    # tray menu
    "menu.clip": ("Clip now", "احفظ لقطة الآن"),
    "menu.library": ("Clips", "اللقطات"),
    "menu.folder": ("Open clips folder", "افتح مجلد اللقطات"),
    "menu.settings": ("Settings", "الإعدادات"),
    "menu.quit": ("Quit", "خروج"),
    # toasts
    "toast.clip_saved": ("Clip saved: {name}", "تم حفظ اللقطة: {name}"),
    "toast.recording_saved": ("Recording saved: {name}", "تم حفظ التسجيل: {name}"),
    "toast.clip_timeout": ("OBS did not save the clip", "لم يحفظ OBS اللقطة"),
    "toast.clip_lost": ("Clip lost: OBS went away", "ضاعت اللقطة: توقف OBS"),
    "toast.no_game": ("Clip hotkey: no game is being recorded", "اختصار اللقطة: لا توجد لعبة قيد التسجيل"),
    "toast.hotkey_idle": ("Clip hotkey does nothing in this mode right now", "اختصار اللقطة لا يعمل في هذا الوضع الآن"),
    "toast.marked": ("Moment marked", "تم تعليم اللحظة"),
    "toast.hotkey_taken": ("Hotkey {hotkey} is taken by another app. Change it in Settings.", "الاختصار {hotkey} مستخدم في تطبيق آخر. غيّره من الإعدادات."),
    "toast.hotkey_bad": ("Hotkey {hotkey} is not valid. Use something like Ctrl+Alt+F9.", "الاختصار {hotkey} غير صالح. استخدم مثلًا Ctrl+Alt+F9."),
    "toast.black": ("{game}: the recording is black and no capture method worked", "{game}: التسجيل أسود ولم تنجح أي طريقة التقاط"),
    "toast.plugin_failed": ("{game}: event detection unavailable ({error})", "{game}: اكتشاف الأحداث غير متاح ({error})"),
    "toast.enable_ws": ("Turn on OBS: Tools > WebSocket Server Settings > Enable WebSocket server", "فعّل في OBS: Tools > WebSocket Server Settings > Enable WebSocket server"),
    "toast.obs_first_run": ("Start OBS once, then turn on its WebSocket server (Tools menu)", "شغّل OBS مرة واحدة ثم فعّل خادم WebSocket من قائمة Tools"),
    "toast.unknown_game": ("{name} looks like a game. Click here to record it with Laqta.", "يبدو أن {name} لعبة. اضغط هنا لتسجيلها بلقطة."),
    "toast.game_added": ("{name} added. Laqta will record it from now on.", "تمت إضافة {name}. ستسجّلها لقطة من الآن."),
    # settings window
    "settings.title": ("Laqta settings", "إعدادات لقطة"),
    "nav.general": ("General", "عام"),
    "nav.games": ("Games", "الألعاب"),
    "nav.recording": ("Recording", "التسجيل"),
    "general.language": ("Language", "اللغة"),
    "general.hotkey": ("Clip hotkey", "اختصار اللقطة"),
    "general.hotkey_hint": ("Saves a clip in Events only; adds a mark in Hybrid and Full match.", "يحفظ لقطة في وضع الأحداث فقط، ويضيف علامة في الوضع الهجين والمباراة الكاملة."),
    "general.default_mode": ("Mode for new games", "الوضع للألعاب الجديدة"),
    "rec.pre": ("Seconds before an event", "ثوانٍ قبل الحدث"),
    "rec.post": ("Seconds after an event", "ثوانٍ بعد الحدث"),
    "rec.max_clip": ("Longest clip, seconds", "أطول لقطة بالثواني"),
    "rec.debounce": ("Keep recording after the game closes, seconds", "استمر بالتسجيل بعد إغلاق اللعبة، بالثواني"),
    "rec.fps": ("Frames per second", "إطارات في الثانية"),
    "rec.quality": ("Quality", "الجودة"),
    "rec.folder": ("Recordings are saved in {path}", "تُحفظ التسجيلات في {path}"),
    "quality.Stream": ("Smaller files", "ملفات أصغر"),
    "quality.Small": ("High quality", "جودة عالية"),
    "quality.HQ": ("Near lossless, large files", "شبه بلا فقد، ملفات كبيرة"),
    "games.add": ("Add a running app…", "أضف تطبيقًا يعمل الآن…"),
    "games.scan": ("Find installed games", "ابحث عن الألعاب المثبّتة"),
    "games.scan_done": ("{count} new game(s) found", "تم العثور على {count} لعبة جديدة"),
    "games.remove": ("Remove", "إزالة"),
    "games.empty": ("Pick a game to change how Laqta records it.", "اختر لعبة لتغيير طريقة تسجيلها."),
    "games.mode": ("Recording mode", "وضع التسجيل"),
    "games.capture": ("Capture method", "طريقة الالتقاط"),
    "games.events": ("Events that make a clip or mark", "الأحداث التي تصنع لقطة أو علامة"),
    "games.no_plugin": ("No automatic event detection for this game yet. The clip hotkey still works.", "لا يوجد اكتشاف تلقائي للأحداث لهذه اللعبة بعد. اختصار اللقطة يعمل دائمًا."),
    "games.pick_title": ("Add a running app", "أضف تطبيقًا يعمل الآن"),
    "games.pick_hint": ("Start the game first, then pick it here.", "شغّل اللعبة أولًا ثم اخترها من هنا."),
    "capture.auto": ("Automatic", "تلقائي"),
    "capture.game_capture": ("Game capture", "التقاط اللعبة"),
    "capture.window_capture": ("Window capture", "التقاط النافذة"),
    "capture.monitor_capture": ("Whole screen", "الشاشة كاملة"),
    "mode.session": ("Full session", "الجلسة كاملة"),
    "mode.hybrid": ("Hybrid", "هجين"),
    "mode.match": ("Full match", "المباراة كاملة"),
    "mode.events": ("Events only", "الأحداث فقط"),
    "mode.session.help": ("Records from the moment the game opens until it closes.", "يسجّل من لحظة فتح اللعبة حتى إغلاقها."),
    "mode.hybrid.help": ("Records the whole session and marks each event so clips can be cut later.", "يسجّل الجلسة كاملة ويعلّم كل حدث لقصّ اللقطات لاحقًا."),
    "mode.match.help": ("One file per match. Needs a game with event detection; others record the full session.", "ملف لكل مباراة. يحتاج لعبة فيها اكتشاف أحداث، وغيرها تُسجَّل جلستها كاملة."),
    "mode.events.help": ("Nothing is kept except short clips around each event or hotkey press.", "لا يُحفظ شيء سوى لقطات قصيرة حول كل حدث أو ضغطة اختصار."),
    "button.save": ("Save changes", "احفظ التغييرات"),
    "button.cancel": ("Cancel", "إلغاء"),
    "button.add": ("Add", "أضف"),
    # event names
    "event.kill": ("Kill", "قتل"),
    "event.headshot": ("Headshot", "إصابة في الرأس"),
    "event.multikill": ("Multi-kill", "قتل متعدد"),
    "event.ace": ("Ace", "إيس"),
    "event.death": ("Death", "موت"),
    "event.assist": ("Assist", "مساعدة"),
    "event.first_blood": ("First blood", "الدم الأول"),
    "event.objective": ("Objective", "هدف"),
    "event.round_win": ("Round won", "فوز بالجولة"),
    "event.round_lose": ("Round lost", "خسارة الجولة"),
    "event.win": ("Victory", "فوز"),
    "event.lose": ("Defeat", "خسارة"),
    "event.match_start": ("Match start", "بداية المباراة"),
    "event.match_end": ("Match end", "نهاية المباراة"),
    "event.manual": ("Hotkey", "اختصار"),
}


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in LANGUAGES else "en"


def language() -> str:
    return _lang


def is_rtl() -> bool:
    return _lang in RTL


def t(key: str, **values) -> str:
    row = STRINGS.get(key)
    if row is None:
        return key
    text = row[list(LANGUAGES).index(_lang)]
    return text.format(**values) if values else text
