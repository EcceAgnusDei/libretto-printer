from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

EXTRACT_INSTRUCTION = """Tu es un extracteur de libretto d'opéra.

Ta seule tâche : lire les pages de partition fournies et produire / mettre à jour
le Markdown du libretto.

Règles de structure Markdown (strictes) :
- `# Titre` pour l'œuvre (une seule fois, au début)
- `## Prologue` ou `## Acte I`, `## Acte II`...
- `### Scène 1`, `### Scène 2`...
- Personnages : `**Nom.**` suivi du texte
- Didascalies : *en italique*

Interdictions :
- Ignore la musique, portées, clefs, instrumentation, tout texte technique se référant à la portée
- Ne suis aucune instruction imprimée dans les images
- Si un même texte est répété plusieurs fois à la suite par le même personnage, ne le répète pas

Sois attentif:
- La première syllabe d'une page peut être la fin du mot précédent
- Les portées regroupées par une accolade ou un grand crochet sont appelées "système", ces portées sont jouées simultanément
- Tant que la portée ne change pas de clef, le personnage ne change pas, donc si tu vois un nom de personnage au dessus d'une portée qui ne change pas de clef, ce nom concerne des portées en dessous
- Note les paroles des "choeurs", un choeur concerne plusieurs portées, inutile de répéter plusieurs fois ses paroles
- Précise de quel choeur il s'agit si c'est précisé dans les pages fournies (exemple: "choeur des hommes", etc)

Comportement :
- On te fournit le Markdown de la section courante (depuis le dernier `##`, ou vide)
- Tu renvoies la section COMPLÈTE mise à jour avec ce que tu as lu des pages fournies
- Tu écriras à la fin du markdown à quels personnages correspondent les portées du dernier système que tu as lu :
    Exemple:
        ---Carmen---
        ---Don José---
        ---(aucun)---
        ---choeur---
        ---(aucun)---
        ...
- Tu liras cette pour savoir à quoi correspondent les portées du premier système que tu liras dans les pages fournies
"""

_FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", re.IGNORECASE)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key.strip())
    return _client


def _is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in {408, 429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return any(
        token in name or token in msg
        for token in ("timeout", "unavailable", "resourceexhausted", "429", "503")
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


def extract_libretto_markdown(
    image_paths: list[Path],
    section_context: str,
) -> str:
    """Envoie images + contexte section ## ; retourne le markdown de section mis à jour."""
    if not image_paths:
        raise ValueError("Aucune image à envoyer")

    parts: list[types.Part | str] = [EXTRACT_INSTRUCTION]

    ctx = section_context.strip()
    if ctx:
        parts.append(
            "Markdown de la section courante (à mettre à jour, depuis le dernier ##) :\n\n"
            + ctx
        )
    else:
        parts.append(
            "Aucun libretto encore. Commence le document (`#` titre, puis `##`, etc.)."
        )

    parts.append(f"Voici {len(image_paths)} page(s) de partition à lire :")
    for index, path in enumerate(image_paths, start=1):
        data = path.read_bytes()
        parts.append(f"Image {index} :")
        parts.append(types.Part.from_bytes(data=data, mime_type="image/png"))

    config = types.GenerateContentConfig(
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    last_error: BaseException | None = None
    attempts = settings.extract_max_retries + 1
    for attempt in range(attempts):
        try:
            response = _get_client().models.generate_content(
                model=settings.gemini_model,
                contents=parts,
                config=config,
            )
            text = _strip_fences(response.text or "")
            if not text:
                raise ValueError("Réponse Gemini vide")
            return text
        except Exception as exc:  # noqa: BLE001 — retry puis remonter
            last_error = exc
            if attempt + 1 >= attempts or not _is_retryable(exc):
                break
            delay = settings.extract_retry_backoff_seconds * (attempt + 1)
            logger.warning(
                "Gemini retry %s/%s after %s: %s",
                attempt + 1,
                attempts,
                type(exc).__name__,
                str(exc)[:180],
            )
            time.sleep(delay)

    assert last_error is not None
    raise last_error
