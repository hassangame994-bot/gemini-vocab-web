from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from PIL import Image
except ImportError:  # Pillow is included in requirements.txt
    Image = None

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
GEMINI_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
HTTP_HEADERS = {
    "User-Agent": "GeminiVocabularyWeb/1.0 (personal vocabulary deck builder)",
}
MAX_WORDS = 80
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 900

app = FastAPI(title="Gemini Vocabulary Cards", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class DetailsRequest(BaseModel):
    api_key: str = Field(min_length=10, max_length=300)
    words: list[str] = Field(min_length=1, max_length=MAX_WORDS)
    skip_common: bool = True


class MediaRequest(BaseModel):
    row: dict[str, Any]
    accent: str = "us"


class ExportRequest(BaseModel):
    deck_name: str = Field(default="English Vocabulary", min_length=1, max_length=100)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_WORDS)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("لم يرجع النموذج قائمة JSON صحيحة")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, list):
        raise ValueError("النتيجة ليست قائمة كلمات")
    return [x for x in parsed if isinstance(x, dict) and str(x.get("word", "")).strip()]


def _gemini_request(api_key: str, prompt: str, image_bytes: bytes | None = None) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if image_bytes is not None:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode("ascii")}})
    errors: list[str] = []
    for model in GEMINI_MODELS:
        try:
            response = requests.post(
                GEMINI_API.format(model=model),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json", **HTTP_HEADERS},
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"},
                },
                timeout=75,
            )
            if response.status_code >= 400:
                try:
                    msg = response.json().get("error", {}).get("message", response.text[:160])
                except Exception:
                    msg = response.text[:160]
                errors.append(f"{model}: HTTP {response.status_code} {str(msg)[:150]}")
                continue
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                errors.append(f"{model}: لم يرجع إجابة")
                continue
            output = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))
            return _extract_json_array(output)
        except requests.RequestException as exc:
            errors.append(f"{model}: {str(exc)[:100]}")
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"{model}: {str(exc)[:100]}")
    # Do not expose the API key. The error messages above never include request headers.
    raise HTTPException(status_code=502, detail="تعذّر الحصول على نتيجة من Gemini. تحقق من المفتاح أو الحصة المتاحة. التفاصيل: " + " | ".join(errors[-2:]))


def _normalize_image(data: bytes, limit: int = MAX_IMAGE_SIDE) -> tuple[bytes, str]:
    if not Image:
        return data, "image/jpeg"
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.thumbnail((limit, limit))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif im.mode == "L":
                im = im.convert("RGB")
            output = io.BytesIO()
            im.save(output, format="JPEG", quality=83, optimize=True)
            return output.getvalue(), "image/jpeg"
    except Exception:
        raise HTTPException(status_code=400, detail="ملف الصورة غير صالح أو صيغته غير مدعومة")


def _build_prompt(words: list[str], skip_common: bool) -> str:
    common_rule = (
        "Skip very common function words such as articles, prepositions, pronouns, auxiliary verbs, "
        "and conjunctions (the, a, of, in, is, and, he, it)."
        if skip_common else "Include every supplied English word or phrase."
    )
    payload = json.dumps(words, ensure_ascii=False)
    return f'''You create high-quality English vocabulary flashcards. Write exactly one object per item in this JSON list, in the same order, copying each word exactly: {payload}
Rules:
- "level": CEFR level: A1, A2, B1, B2, C1, or C2.
- "meaning": a clear English definition at the same difficulty level; A1-A2 definitions must use very simple common English. Never use the target word or its forms inside the definition.
- "example": one natural, concrete English sentence of 6 to 14 words containing the exact word or phrase in its normal meaning. Avoid generic sentences.
- "image_query": 3 to 6 English words describing a clear concrete photo that fits the meaning and the example. Avoid text, logos, charts, and ambiguous scenes.
- {common_rule}
Return only a JSON array. Each object must contain: word, level, meaning, example, image_query.'''


def _extract_prompt(skip_common: bool) -> str:
    common_rule = (
        "Skip very common function words (articles, prepositions, pronouns, auxiliary verbs, conjunctions: the, a, of, in, is, and, he, it, ...)."
        if skip_common else "Include all English words in the image."
    )
    return f'''You are building English vocabulary flashcards. Read the uploaded picture carefully; it may be a book page, screenshot, photo, table, or vocabulary list.
Rules:
1. Extract English words and vocabulary phrases only. Ignore Arabic and other languages, numbers, page numbers, and symbols.
2. If it is a vocabulary list/table, return one item per listed item and keep multi-word phrases together.
3. If it is running text, return each distinct useful word once, in order of appearance. Do not return duplicates.
4. Correct obvious spelling/OCR errors and skip broken fragments.
5. {common_rule}
For each word, include:
- level: CEFR A1-C2.
- meaning: a short English definition suitable for that level. Never use the target word or its forms in the definition.
- example: one natural, concrete English sentence of 6-14 words containing the word.
- image_query: 3-6 English words describing a concrete photo that explains the word.
Return ONLY a JSON array. Example: [{{"word":"apple","level":"A1","meaning":"a round fruit that can be red or green","example":"She packed a red apple for her school lunch.","image_query":"red apple on wooden table"}}]. If there is no English text, return [].'''


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/extract")
async def extract_words(
    api_key: str = Form(...),
    skip_common: bool = Form("true"),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    if len(api_key.strip()) < 10:
        raise HTTPException(status_code=400, detail="أدخل مفتاح Gemini API الصحيح")
    if not files or len(files) > 8:
        raise HTTPException(status_code=400, detail="ارفع من صورة إلى 8 صور في المرة الواحدة")
    skip = str(skip_common).lower() in {"1", "true", "yes", "on"}
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    for file in files:
        content = await file.read()
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="حجم الصور الكلي يجب ألا يتجاوز 8 ميجابايت")
        if file.content_type and not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="ارفع ملفات صور فقط")
        normalized, _mime = _normalize_image(content, limit=1800)
        try:
            entries = _gemini_request(api_key.strip(), _extract_prompt(skip), normalized)
        except HTTPException:
            raise
        for item in entries:
            word = re.sub(r"\s+", " ", str(item.get("word", "")).strip())
            key = word.casefold()
            if not word or key in seen or not re.search(r"[A-Za-z]", word):
                continue
            seen.add(key)
            all_rows.append({
                "word": word,
                "level": str(item.get("level", "")).strip(),
                "meaning": str(item.get("meaning", "")).strip(),
                "example": str(item.get("example", "")).strip(),
                "image_query": str(item.get("image_query", word)).strip() or word,
                "form": str(item.get("form", "")).strip(),
                "image_data": "", "audio_data": "", "image_source": "", "image_title": "",
                "image_error": "", "audio_error": "",
            })
            if len(all_rows) >= MAX_WORDS:
                break
        if len(all_rows) >= MAX_WORDS:
            break
    return {"rows": all_rows, "count": len(all_rows), "message": "تم استخراج الكلمات" if all_rows else "لم يتم العثور على كلمات إنجليزية واضحة"}


@app.post("/api/details")
def generate_details(request: DetailsRequest) -> dict[str, Any]:
    clean_words: list[str] = []
    seen: set[str] = set()
    for raw in request.words:
        word = re.sub(r"\s+", " ", str(raw)).strip()
        if word and re.search(r"[A-Za-z]", word) and word.casefold() not in seen:
            clean_words.append(word)
            seen.add(word.casefold())
    if not clean_words:
        raise HTTPException(status_code=400, detail="اكتب كلمة إنجليزية واحدة على الأقل")
    entries = _gemini_request(request.api_key.strip(), _build_prompt(clean_words, request.skip_common))
    lookup = {str(item.get("word", "")).strip().casefold(): item for item in entries}
    rows = []
    for word in clean_words:
        item = lookup.get(word.casefold(), {})
        rows.append({
            "word": word,
            "level": str(item.get("level", "")).strip(),
            "meaning": str(item.get("meaning", "")).strip(),
            "example": str(item.get("example", "")).strip(),
            "image_query": str(item.get("image_query", word)).strip() or word,
            "form": "", "image_data": "", "audio_data": "", "image_source": "", "image_title": "",
            "image_error": "", "audio_error": "",
        })
    return {"rows": rows, "count": len(rows)}


def _commons_image(query: str) -> tuple[bytes, str, str] | None:
    params = {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo",
        "iiprop": "url", "iiurlwidth": MAX_IMAGE_SIDE, "format": "json",
    }
    response = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers=HTTP_HEADERS, timeout=18)
    response.raise_for_status()
    pages = list((response.json().get("query", {}).get("pages") or {}).values())
    pages.sort(key=lambda x: x.get("index", 9999))
    for page in pages:
        title = str(page.get("title", ""))
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        url = info.get("thumburl") or info.get("url")
        if not url or str(url).lower().split("?")[0].endswith(".svg"):
            continue
        try:
            img_response = requests.get(url, headers=HTTP_HEADERS, timeout=15)
            img_response.raise_for_status()
            if not img_response.content or len(img_response.content) > 15 * 1024 * 1024:
                continue
            normalized, _ = _normalize_image(img_response.content)
            source_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_:()")
            return normalized, source_url, title
        except (requests.RequestException, HTTPException, ValueError):
            continue
    return None


def _make_audio(word: str, accent: str = "us") -> bytes:
    word = re.sub(r"\s+", " ", word).strip()[:180]
    if not word:
        raise RuntimeError("الكلمة فارغة")
    domain = "co.uk" if accent == "uk" else "com"
    errors: list[str] = []
    try:
        response = requests.get(
            f"https://translate.google.{domain}/translate_tts",
            params={"ie": "UTF-8", "client": "tw-ob", "tl": "en", "q": word},
            headers={**HTTP_HEADERS, "Referer": f"https://translate.google.{domain}/"}, timeout=22,
        )
        response.raise_for_status()
        data = response.content
        if len(data) > 700 and (data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)):
            return data
        errors.append("Google TTS did not return audio")
    except Exception as exc:
        errors.append(str(exc)[:80])
    try:
        dictionary = requests.get(
            "https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(word.lower()),
            headers=HTTP_HEADERS, timeout=15,
        )
        dictionary.raise_for_status()
        urls: list[str] = []
        suffix = "-uk" if accent == "uk" else "-us"
        for entry in dictionary.json():
            for phonetic in entry.get("phonetics", []):
                url = phonetic.get("audio")
                if url:
                    urls.append(url)
        urls.sort(key=lambda url: 0 if suffix in url.lower() else 1)
        for url in urls:
            try:
                audio_response = requests.get(url, headers=HTTP_HEADERS, timeout=15)
                audio_response.raise_for_status()
                data = audio_response.content
                if len(data) > 700 and (data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)):
                    return data
            except requests.RequestException:
                pass
    except Exception as exc:
        errors.append(str(exc)[:80])
    raise RuntimeError("تعذّر إنشاء الصوت؛ " + " / ".join(errors[-2:]))


@app.post("/api/media-one")
def make_one_media(request: MediaRequest) -> dict[str, Any]:
    row = request.row
    word = re.sub(r"\s+", " ", str(row.get("word", "")).strip())[:180]
    if not word or not re.search(r"[A-Za-z]", word):
        raise HTTPException(status_code=400, detail="الكلمة غير صالحة")
    result: dict[str, Any] = {"image_data": "", "audio_data": "", "image_source": "", "image_title": "", "image_error": "", "audio_error": ""}
    query = str(row.get("image_query") or word).strip()[:200]
    try:
        found = _commons_image(query)
        if not found and query.casefold() != word.casefold():
            found = _commons_image(word)
        if found:
            data, source, title = found
            result.update(image_data="data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"), image_source=source, image_title=title)
        else:
            result["image_error"] = "لم أجد صورة مناسبة في Wikimedia Commons"
    except Exception as exc:
        result["image_error"] = "فشل البحث عن صورة: " + str(exc)[:120]
    try:
        audio_data = _make_audio(word, "uk" if request.accent == "uk" else "us")
        result["audio_data"] = "data:audio/mpeg;base64," + base64.b64encode(audio_data).decode("ascii")
    except Exception as exc:
        result["audio_error"] = str(exc)[:180]
    return result


def _data_url_bytes(value: Any) -> tuple[bytes, str] | None:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None
    header, encoded = value.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception:
        return None
    return data, mime


def _safe_field(value: Any) -> str:
    return html.escape(str(value or "").strip(), quote=False).replace("\r\n", "\n").replace("\n", "<br>")


def _format_example(sentence: str, word: str, form: str = "") -> str:
    safe_sentence = html.escape(str(sentence or "").strip(), quote=False)
    for candidate in (form, word):
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        parts = candidate.split()
        pattern = r"(?<![A-Za-z])" + r"\s+".join(re.escape(html.escape(p)) for p in parts) + r"(?![A-Za-z])"
        match = re.search(pattern, safe_sentence, flags=re.I)
        if match:
            return safe_sentence[:match.start()] + "<b>" + match.group(0) + "</b>" + safe_sentence[match.end():]
    return safe_sentence


def _write_apkg(deck_name: str, rows: list[dict[str, Any]]) -> str:
    """Create a small Anki .apkg package with embedded media using stdlib only."""
    timestamp = int(time.time())
    now_ms = int(time.time() * 1000)
    deck_id = int(hashlib.sha1((deck_name + str(now_ms)).encode("utf-8")).hexdigest()[:8], 16) + 100
    model_id = deck_id + 1
    media_map: dict[str, str] = {}
    media_blobs: dict[str, bytes] = {}
    notes: list[dict[str, str]] = []
    seen_words: set[str] = set()

    for row in rows:
        word = re.sub(r"\s+", " ", str(row.get("word", "")).strip())[:200]
        if not word or not re.search(r"[A-Za-z]", word) or word.casefold() in seen_words:
            continue
        seen_words.add(word.casefold())
        picture_field = ""
        audio_field = ""
        img = _data_url_bytes(row.get("image_data"))
        if img and img[1].startswith("image/") and len(img[0]) <= 8 * 1024 * 1024:
            data, _mime = _normalize_image(img[0])
            filename = "vocab_img_" + hashlib.sha1((word.casefold() + str(len(data))).encode()).hexdigest()[:14] + ".jpg"
            media_blobs.setdefault(filename, data)
            picture_field = f'<img src="{filename}">'
        audio = _data_url_bytes(row.get("audio_data"))
        if audio and audio[1].startswith("audio/") and len(audio[0]) <= 4 * 1024 * 1024:
            data = audio[0]
            # Audio source should be MP3; the generated engine validates MPEG frame headers.
            filename = "vocab_audio_" + hashlib.sha1(word.casefold().encode()).hexdigest()[:14] + ".mp3"
            media_blobs.setdefault(filename, data)
            audio_field = f"[sound:{filename}]"
        image_source = str(row.get("image_source", "")).strip()
        image_title = str(row.get("image_title", "")).strip()
        source_field = ""
        if image_source.startswith("https://commons.wikimedia.org/"):
            safe_url = html.escape(image_source, quote=True)
            safe_title = html.escape(image_title or "Wikimedia Commons", quote=False)
            source_field = f'<a href="{safe_url}">{safe_title}</a>'
        notes.append({
            "picture": picture_field,
            "audio": audio_field,
            "word": html.escape(word, quote=False),
            "level": _safe_field(row.get("level")),
            "meaning": _safe_field(row.get("meaning")),
            "example": _format_example(str(row.get("example", "")), word, str(row.get("form", ""))),
            "source": source_field,
        })
    if not notes:
        raise HTTPException(status_code=400, detail="لا توجد كلمات صالحة لإنشاء البطاقات")

    fields = [
        {"name": "Picture", "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        {"name": "Audio", "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        {"name": "Word", "ord": 2, "sticky": False, "rtl": False, "font": "Arial", "size": 24, "media": []},
        {"name": "Level", "ord": 3, "sticky": False, "rtl": False, "font": "Arial", "size": 18, "media": []},
        {"name": "Definition", "ord": 4, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        {"name": "Example", "ord": 5, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        {"name": "ImageSource", "ord": 6, "sticky": False, "rtl": False, "font": "Arial", "size": 12, "media": []},
    ]
    qfmt = '<div class="picture">{{Picture}}</div><div class="audio">{{Audio}}</div><span class="word-hidden">{{Word}}</span>'
    afmt = '{{FrontSide}}<hr id="answer"><div class="word">{{Word}}</div>{{#Level}}<div class="level">{{Level}}</div>{{/Level}}{{#Definition}}<div class="definition">{{Definition}}</div>{{/Definition}}{{#Example}}<div class="example">{{Example}}</div>{{/Example}}{{#ImageSource}}<div class="source">Image: {{ImageSource}}</div>{{/ImageSource}}'
    model = {
        "css": ".card { font-family: Arial; font-size: 20px; text-align: center; color: #1e293b; background: #fff; } .picture img { max-width: 100%; max-height: 58vh; object-fit: contain; border-radius: 12px; } .audio { margin-top: 8px; } .word-hidden { display:none; } .word { font-size: 30px; font-weight: 700; margin: 14px 0; } .level { color: #64748b; font-size: 14px; margin-bottom: 10px; } .definition { line-height: 1.5; margin: 12px 0; } .example { margin-top: 18px; line-height: 1.5; color: #475569; } .source { font-size: 10px; margin-top: 18px; overflow-wrap: anywhere; }",
        "did": deck_id, "flds": fields, "id": str(model_id), "latexPost": "\\end{document}",
        "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n",
        "latexsvg": False, "mod": timestamp, "name": "Gemini Vocabulary - Image First",
        "req": [[0, "any", [0, 1, 2]]], "sortf": 2, "tags": [],
        "tmpls": [{"name": "Image → Word", "ord": 0, "qfmt": qfmt, "afmt": afmt, "bafmt": "", "bqfmt": "", "bfont": "Arial", "bsize": 20, "did": None}],
        "type": 0, "usn": -1, "vers": [],
    }
    deck = {
        "collapsed": False, "conf": 1, "desc": "Cards generated with Gemini Vocabulary Cards Web",
        "dyn": 0, "extendNew": 10, "extendRev": 50, "id": deck_id,
        "lrnToday": [0, 0], "mod": timestamp, "name": deck_name, "newToday": [0, 0],
        "revToday": [0, 0], "timeToday": [0, 0], "usn": 0,
    }
    conf = {
        "activeDecks": [deck_id], "addToCur": True, "collapseTime": 1200, "curDeck": deck_id,
        "curModel": str(model_id), "dueCounts": True, "estTimes": True, "newBury": True,
        "newSpread": 0, "nextPos": len(notes) + 1, "sortBackwards": False, "sortType": "noteFld", "timeLim": 0,
    }
    dconf = {"1": {
        "autoplay": True, "id": 1, "lapse": {"delays": [10], "leechAction": 0, "leechFails": 8, "minInt": 1, "mult": 0},
        "maxTaken": 60, "mod": 0, "name": "Default", "new": {"bury": True, "delays": [1, 10], "initialFactor": 2500, "ints": [1, 4, 7], "order": 1, "perDay": 20, "separate": True},
        "replayq": True, "rev": {"bury": True, "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1, "maxIvl": 36500, "minSpace": 1, "perDay": 100}, "timer": 0, "usn": 0,
    }}

    tmp_dir = Path(tempfile.mkdtemp(prefix="vocab_apkg_"))
    db_path = tmp_dir / "collection.anki2"
    apkg_path = tmp_dir / "vocabulary.apkg"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
        CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null, ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null, models text not null, decks text not null, dconf text not null, tags text not null);
        CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null, usn integer not null, tags text not null, flds text not null, sfld integer not null, csum integer not null, flags integer not null, data text not null);
        CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null, mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null, ivl integer not null, factor integer not null, reps integer not null, lapses integer not null, left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
        CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null, time integer not null, type integer not null);
        CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
        CREATE INDEX ix_notes_usn on notes (usn); CREATE INDEX ix_cards_usn on cards (usn); CREATE INDEX ix_revlog_usn on revlog (usn); CREATE INDEX ix_cards_nid on cards (nid); CREATE INDEX ix_cards_sched on cards (did, queue, due); CREATE INDEX ix_revlog_cid on revlog (cid); CREATE INDEX ix_notes_csum on notes (csum);
        """)
        conn.execute("INSERT INTO col VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)", (
            timestamp, now_ms, now_ms, 11, 0, 0, 0, json.dumps(conf, ensure_ascii=False),
            json.dumps({str(model_id): model}, ensure_ascii=False), json.dumps({str(deck_id): deck}, ensure_ascii=False),
            json.dumps(dconf, ensure_ascii=False), "{}",
        ))
        id_gen = iter(range(now_ms, now_ms + len(notes) * 3 + 20))
        for position, note in enumerate(notes, start=1):
            note_id = next(id_gen)
            word = html.unescape(note["word"])
            guid = hashlib.sha1(word.casefold().encode("utf-8")).hexdigest()[:10]
            values = [note["picture"], note["audio"], note["word"], note["level"], note["meaning"], note["example"], note["source"]]
            fields_text = "\x1f".join(values)
            csum = int(hashlib.sha1(word.encode("utf-8")).hexdigest()[:8], 16)
            conn.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                note_id, guid, model_id, timestamp, -1, " vocab_web ", fields_text, word, csum, 0, "",
            ))
            card_id = next(id_gen)
            conn.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                card_id, note_id, deck_id, 0, timestamp, -1, 0, 0, position, 0, 2500, 0, 0, 0, 0, 0, 0, "",
            ))
        conn.commit()
        conn.execute("PRAGMA integrity_check")
    finally:
        conn.close()

    with zipfile.ZipFile(apkg_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(db_path, "collection.anki2")
        names = list(media_blobs.keys())
        media_map: dict[str, str] = {}
        for index, filename in enumerate(names):
            media_map[str(index)] = filename
            archive.writestr(str(index), media_blobs[filename])
        archive.writestr("media", json.dumps(media_map, ensure_ascii=False))
    return str(apkg_path)


def _cleanup_export(path: str) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)


@app.post("/api/export-apkg")
def export_apkg(request: ExportRequest) -> FileResponse:
    path = _write_apkg(request.deck_name.strip() or "English Vocabulary", request.rows)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename="english_vocabulary.apkg",
        background=BackgroundTask(_cleanup_export, path),
    )
