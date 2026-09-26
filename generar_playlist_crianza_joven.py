"""
generar_playlist_crianza_joven.py
=================================

Qué hace este script (en una frase):
Revisa la playlist de YouTube "Crianza Joven", descarta los vídeos que ya
no están disponibles, y SOLO SI la vuelta anterior ya ha terminado, genera
un nuevo orden aleatorio y lo sube a GitHub.

Está pensado para ejecutarse periódicamente (por ejemplo cada 15-30 minutos)
mediante un cron o una GitHub Action programada. Si la vuelta actual todavía
no ha terminado, el script no hace nada (no gasta cuota de la API ni hace
cambios innecesarios en GitHub).

CONFIGURACIÓN NECESARIA (variables de entorno / "secrets"):
  YOUTUBE_API_KEY        -> tu clave de la API de YouTube Data v3
  YOUTUBE_PLAYLIST_ID    -> PLWKLKfFKPoEQ   (playlist "Crianza Joven")
  GITHUB_TOKEN           -> token de GitHub con permiso de escritura en el repo
  GITHUB_REPO            -> "portalarioja-dev/NOMBRE_DEL_REPO"
  GITHUB_JSON_PATH       -> ruta del archivo dentro del repo,
                            por ejemplo "crianza-joven/playlist.json"

El archivo JSON resultante tiene esta forma:

{
  "generated_at": 1730000000,       <- momento (epoch, segundos UTC) en que se barajó
  "total_duration": 15234,          <- duración total de la vuelta completa, en segundos
  "items": [
    {"videoId": "xxxx", "title": "Nombre de la canción", "duration": 214},
    ...
  ]
}

El widget del blog usa "generated_at" y "total_duration" para saber, en
cualquier momento, "cuántos segundos llevamos de vuelta" y así todos los
oyentes escuchan exactamente lo mismo a la vez.
"""

import os
import sys
import json
import random
import base64
import time
import re
import urllib.request
import urllib.error

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
PLAYLIST_ID = os.environ.get("YOUTUBE_PLAYLIST_ID", "PLWKLKfFKPoEQ")
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]              # "portalarioja-dev/crianza-joven"
GITHUB_JSON_PATH = os.environ.get("GITHUB_JSON_PATH", "crianza-joven/playlist.json")

YT_API = "https://www.googleapis.com/youtube/v3"
GH_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_JSON_PATH}"


def yt_get(endpoint, params):
    params = dict(params)
    params["key"] = YOUTUBE_API_KEY
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{YT_API}/{endpoint}?{query}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


import urllib.parse  # noqa: E402  (colocado aquí para mantener el orden de lectura de arriba)


def fetch_playlist_video_ids():
    """Devuelve la lista de videoId de la playlist, en el orden original."""
    ids = []
    page_token = ""
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        data = yt_get("playlistItems", params)
        for item in data.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                ids.append(vid)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids


def parse_iso8601_duration(duration):
    """Convierte 'PT3M45S' -> 225 (segundos)."""
    match = re.match(
        r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration
    )
    if not match:
        return 0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s


def fetch_available_videos(video_ids):
    """
    Consulta videos.list en bloques de 50. Cualquier videoId que NO aparezca
    en la respuesta se considera borrado/privado y se descarta.
    """
    available = {}
    for i in range(0, len(video_ids), 50):
        block = video_ids[i : i + 50]
        data = yt_get(
            "videos",
            {
                "part": "snippet,contentDetails,status",
                "id": ",".join(block),
            },
        )
        for item in data.get("items", []):
            status = item.get("status", {})
            if status.get("privacyStatus") not in ("public", "unlisted"):
                continue  # privado -> descartado
            vid = item["id"]
            title = item["snippet"]["title"]
            duration = parse_iso8601_duration(
                item["contentDetails"]["duration"]
            )
            if duration <= 0:
                continue
            available[vid] = {"videoId": vid, "title": title, "duration": duration}
    return available


def github_get_current():
    """Descarga el JSON actual (si existe) junto con su 'sha' para poder actualizarlo."""
    req = urllib.request.Request(
        GH_API,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def github_put(new_content, sha):
    body = {
        "message": "Nueva vuelta aleatoria de Crianza Joven",
        "content": base64.b64encode(
            json.dumps(new_content, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        GH_API,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()


def build_new_order(available_videos):
    items = list(available_videos.values())
    random.shuffle(items)
    total_duration = sum(v["duration"] for v in items)
    return {
        "generated_at": int(time.time()),
        "total_duration": total_duration,
        "items": items,
    }


def main():
    current, sha = github_get_current()

    if current is not None:
        elapsed = time.time() - current["generated_at"]
        if elapsed < current["total_duration"]:
            restante = int(current["total_duration"] - elapsed)
            print(
                f"La vuelta actual todavía no ha terminado "
                f"(quedan {restante} segundos). No se hace nada."
            )
            return
        print("La vuelta actual ha terminado. Generando una nueva baraja...")
    else:
        print("No existe playlist.json todavía. Generando la primera baraja...")

    video_ids = fetch_playlist_video_ids()
    print(f"La playlist tiene {len(video_ids)} vídeos en total.")

    available = fetch_available_videos(video_ids)
    descartados = len(video_ids) - len(available)
    print(f"Disponibles: {len(available)}  |  Descartados (borrados/privados): {descartados}")

    if not available:
        print("ERROR: no ha quedado ningún vídeo disponible. Abortando.")
        sys.exit(1)

    new_content = build_new_order(available)
    github_put(new_content, sha)
    print(
        f"Nueva baraja subida a GitHub. "
        f"Duración total de la vuelta: {new_content['total_duration']} segundos "
        f"({len(new_content['items'])} vídeos)."
    )


if __name__ == "__main__":
    main()
