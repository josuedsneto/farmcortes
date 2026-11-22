from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .pipeline import Caption, PipelineError, probe, run, write_ass
from .youtube import publish_file, settings as youtube_settings


USER_AGENT = "Farmcortes/1.0 (automated Wikimedia research; private operator)"
BRAZIL_HINTS = {
    "brasil", "brasileiro", "brasileira", "são paulo", "rio de janeiro", "bahia",
    "pernambuco", "minas gerais", "paraná", "amazonas", "gaúcho", "português",
}
AVOID_HINTS = {
    "guerra", "batalha", "mata", "matando", "morreram", "execução", "massacre",
    "bomba atômica", "terremoto", "acidente", "colide", "cai perto",
}


@dataclass(frozen=True)
class Story:
    year: int
    event: str
    title: str
    extract: str
    article_url: str
    image_url: str
    image_page_url: str


def _json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def research_on_this_day(day: date) -> Story:
    url = f"https://pt.wikipedia.org/api/rest_v1/feed/onthisday/events/{day:%m/%d}"
    payload = _json(url)
    choices: list[tuple[float, dict, dict]] = []
    for event in payload.get("events", []):
        pages = [page for page in event.get("pages", []) if page.get("originalimage") or page.get("thumbnail")]
        if not pages:
            continue
        page = pages[0]
        focus_text = f"{event.get('text', '')} {page.get('titles', {}).get('normalized', '')}".casefold()
        text = f"{focus_text} {page.get('extract', '')}".casefold()
        score = 2.0 + min(len(text), 900) / 900
        score += 3.2 * sum(hint in focus_text for hint in BRAZIL_HINTS)
        score -= 1.4 * sum(hint in text for hint in AVOID_HINTS)
        if 1700 <= int(event.get("year", 0)) <= 1999:
            score += 0.8
        choices.append((score, event, page))
    if not choices:
        raise PipelineError("A pesquisa não encontrou uma pauta com imagem para esta data")
    _, event, page = max(choices, key=lambda item: item[0])
    image_url = (page.get("originalimage") or page["thumbnail"])["source"]
    image_path = urllib.parse.unquote(urllib.parse.urlparse(image_url).path)
    filename = image_path.rsplit("/", 1)[-1]
    image_page_url = "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(filename)
    return Story(
        year=int(event["year"]),
        event=re.sub(r"\s+", " ", event["text"]).strip(),
        title=page.get("titles", {}).get("normalized") or page.get("title", "História do dia").replace("_", " "),
        extract=re.sub(r"\s+", " ", page.get("extract", "")).strip(),
        article_url=page.get("content_urls", {}).get("desktop", {}).get("page", "https://pt.wikipedia.org"),
        image_url=image_url,
        image_page_url=image_page_url,
    )


def build_script(story: Story, day: date) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", story.extract)
    selected: list[str] = []
    seen: set[str] = set()
    for sentence in [story.event, *sentences]:
        clean = re.sub(r"\s+", " ", sentence).strip()
        signature = re.sub(r"\W+", " ", clean.casefold())[:70]
        if clean and signature not in seen:
            selected.append(clean)
            seen.add(signature)
        if sum(len(item.split()) for item in selected) >= 82:
            break
    month_names = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    hook = f"Você sabia que, em {day.day} de {month_names[day.month]} de {story.year}, aconteceu algo que ainda marca a história?"
    body = " ".join(selected)
    script = f"{hook} {body} Esta foi a história de hoje. Siga o canal para descobrir a próxima."
    words = script.split()
    return " ".join(words[:135])


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as target:
        while chunk := response.read(1024 * 1024):
            target.write(chunk)
    if destination.stat().st_size < 10_000:
        raise PipelineError("A imagem pesquisada não pôde ser baixada")


def synthesize(script: str, output: Path, model: Path) -> None:
    input_file = output.with_suffix(".txt")
    input_file.write_text(script, encoding="utf-8")
    run([
        str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "piper"),
        "--model", str(model), "--input-file", str(input_file), "--output-file", str(output),
        "--sentence-silence", "0.22", "--length-scale", "0.94",
    ], timeout=900)


def timed_captions(script: str, duration: float) -> list[Caption]:
    words = script.split()
    groups = [words[index:index + 4] for index in range(0, len(words), 4)]
    weights = [max(1, sum(len(word) + 1 for word in group)) for group in groups]
    total = sum(weights)
    cursor = 0.0
    captions = []
    for index, (group, weight) in enumerate(zip(groups, weights)):
        end = duration if index == len(groups) - 1 else cursor + duration * weight / total
        captions.append(Caption(cursor, end, " ".join(group)))
        cursor = end
    return captions


def _escape_filter(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_original(image: Path, audio: Path, script: str, title: str, output: Path) -> None:
    duration = float(probe(audio).get("format", {}).get("duration") or 0)
    if duration < 15 or duration > 180:
        raise PipelineError(f"A narração ficou fora da duração esperada: {duration:.1f}s")
    subtitle = output.with_suffix(".ass")
    write_ass(timed_captions(script, duration), subtitle)
    title_file = output.with_suffix(".title.txt")
    title_file.write_text(title.upper(), encoding="utf-8")
    poster = output.with_suffix(".poster.jpg")
    poster_filters = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=28,eq=brightness=-0.28[back];"
        "[fg]scale=980:1180:force_original_aspect_ratio=decrease[front];"
        "[back][front]overlay=(W-w)/2:(H-h)/2,"
        "drawbox=x=0:y=0:w=iw:h=340:color=black@0.32:t=fill,"
        "drawbox=x=0:y=1510:w=iw:h=410:color=black@0.62:t=fill,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:textfile='{_escape_filter(title_file)}':"
        r"fontcolor=white:fontsize=64:line_spacing=12:x=max(60\,(w-text_w)/2):y=1590:fix_bounds=1[v]"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(image),
        "-filter_complex", poster_filters, "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(poster),
    ], timeout=600)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(poster), "-i", str(audio),
        "-t", f"{duration:.3f}", "-vf", f"subtitles='{_escape_filter(subtitle)}'", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:LRA=9:TP=-1.5", "-movflags", "+faststart", str(output),
    ], timeout=3600)
    info = probe(output)
    video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), {})
    if video.get("width") != 1080 or video.get("height") != 1920:
        raise PipelineError("O vídeo original não ficou em 1080x1920")


def metadata(story: Story, day: date) -> dict:
    title = f"{story.title}: aconteceu hoje na história #Shorts"[:100]
    description = (
        "Inscreva-se no canal para acompanhar os próximos vídeos.\n\n"
        f"Em {day:%d/%m}, este fato entrou para a história.\n\n"
        f"Fonte e leitura complementar: {story.article_url}\n"
        f"Imagem e créditos/licença: {story.image_page_url}\n\n"
        "Roteiro, narração e edição produzidos automaticamente pelo Coletivo Cortes com fontes abertas.\n\n"
        "#Shorts #História #HojeNaHistória #ColetivoCortes"
    )
    return {
        "title": title, "description": description,
        "tags": ["Shorts", "história", "hoje na história", "Coletivo Cortes"],
        "category_id": "27", "language": "pt-BR", "made_for_kids": False,
        "contains_synthetic_media": False,
    }


def generate(data_dir: Path, day: date, *, publish: bool = False) -> dict:
    story = research_on_this_day(day)
    folder = data_dir / "originals" / f"{day:%Y-%m-%d}-{re.sub(r'[^a-z0-9]+', '-', story.title.casefold()).strip('-')[:42]}"
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "source-image.jpg"
    audio = folder / "narration.wav"
    video = folder / "short.mp4"
    script = build_script(story, day)
    _download(story.image_url, image)
    model = data_dir / "models" / "pt_BR-edresson-low.onnx"
    if not model.is_file():
        raise PipelineError("Modelo de voz pt-BR não está instalado")
    synthesize(script, audio, model)
    render_original(image, audio, script, story.title, video)
    result = {
        "status": "rendered", "story": asdict(story), "script": script,
        "video": str(video), "metadata": metadata(story, day), "publication": {},
    }
    if publish:
        config = youtube_settings(data_dir)
        result["publication"] = publish_file(video, result["metadata"], token=config["token"], privacy=config["privacy"])
        result["status"] = "published"
    (folder / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Pesquisa, gera e opcionalmente publica um Short original")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    print(json.dumps(generate(args.data_dir.resolve(), args.date, publish=args.publish), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
