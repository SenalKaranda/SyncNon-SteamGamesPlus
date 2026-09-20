from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Callable


@dataclass
class GridCandidate:
    game_id: str
    game_name: str


@dataclass
class GridPick:
    skip: bool = False
    game_id: str | None = None
    game_name: str | None = None
    apply_to_rest: bool = False


def extract_candidates(payload: dict | None, limit: int = 12) -> list[GridCandidate]:
    if not payload or not payload.get("success"):
        return []
    rows = payload.get("data") or []
    candidates: list[GridCandidate] = []
    seen: set[str] = set()
    for row in rows:
        game_id = str(row.get("id", "")).strip()
        game_name = str(row.get("name", "")).strip()
        if not game_id or not game_name:
            continue
        key = f"{game_id}:{game_name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(GridCandidate(game_id=game_id, game_name=game_name))
        if len(candidates) >= limit:
            break
    return candidates


def normalize_title(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[:®™©_/\\|()\[\]!,.?-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_title_match(query: str, candidate: str) -> float:
    query_norm = normalize_title(query)
    candidate_norm = normalize_title(candidate)
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 100.0

    ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio() * 100
    query_tokens = query_norm.split()
    candidate_tokens = candidate_norm.split()
    if query_tokens and candidate_tokens:
        if query_tokens[0] == candidate_tokens[0]:
            ratio += 25
        else:
            ratio -= 35
        overlap = len(set(query_tokens) & set(candidate_tokens))
        ratio += overlap * 4
    return ratio


def rank_candidates(query: str, candidates: list[GridCandidate]) -> list[GridCandidate]:
    return sorted(candidates, key=lambda item: (-score_title_match(query, item.game_name), item.game_name.lower()))


def titles_look_similar(left: str, right: str, threshold: float = 55.0) -> bool:
    return score_title_match(left, right) >= threshold


def pick_best_candidate(query: str, candidates: list[GridCandidate]) -> GridCandidate | None:
    ranked = rank_candidates(query, candidates)
    return ranked[0] if ranked else None


def prompt_grid_match_cli(
    discovered_name: str,
    folder_name: str,
    current_appname: str | None,
    candidates: list[GridCandidate],
    search_fn: Callable[[str], list[GridCandidate]],
    input_fn=input,
    print_fn=print,
) -> GridPick:
    current = list(candidates)
    query = discovered_name
    while True:
        print_fn("")
        print_fn(f"Discovered: {discovered_name}")
        if folder_name and folder_name != discovered_name:
            print_fn(f"Folder: {folder_name}")
        if current_appname:
            print_fn(f"Currently in Steam as: {current_appname}")
        if not current:
            print_fn("No SteamGridDB results. Enter a search, s to skip, or n to use the discovered name.")
        else:
            print_fn("SteamGridDB matches:")
            for index, candidate in enumerate(current, start=1):
                marker = " (suggested)" if index == 1 else ""
                print_fn(f"  {index}. {candidate.game_name}{marker}")
            print_fn("Enter a number, a new search, s to skip, n for discovered name, a to auto-accept remaining.")

        raw = (input_fn("> ") or "").strip()
        if not raw:
            if current:
                best = current[0]
                return GridPick(game_id=best.game_id, game_name=best.game_name)
            continue
        lowered = raw.lower()
        if lowered in {"s", "skip"}:
            return GridPick(skip=True)
        if lowered in {"n", "name", "discovered"}:
            return GridPick(game_id=None, game_name=discovered_name)
        if lowered in {"a", "auto"}:
            if not current:
                continue
            best = current[0]
            return GridPick(game_id=best.game_id, game_name=best.game_name, apply_to_rest=True)
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(current):
                chosen = current[index - 1]
                return GridPick(game_id=chosen.game_id, game_name=chosen.game_name)
            print_fn("That number is not in the list.")
            continue
        query = raw
        current = rank_candidates(query, search_fn(query))


def prompt_grid_match_gui(
    discovered_name: str,
    folder_name: str,
    current_appname: str | None,
    candidates: list[GridCandidate],
    search_fn: Callable[[str], list[GridCandidate]],
) -> GridPick:
    import tkinter as tk
    from tkinter import ttk

    result = {"pick": GridPick(skip=True)}
    current = list(candidates)

    root = tk.Tk()
    root.title("Confirm SteamGridDB match")
    root.resizable(True, True)
    root.minsize(520, 360)

    info_lines = [f"Discovered as: {discovered_name}"]
    if folder_name and folder_name != discovered_name:
        info_lines.append(f"Folder: {folder_name}")
    if current_appname:
        info_lines.append(f"Currently in Steam as: {current_appname}")

    ttk.Label(root, text="\n".join(info_lines), justify="left").pack(anchor="w", padx=12, pady=(12, 8))
    ttk.Label(root, text="Choose the SteamGridDB title to use for this game:").pack(anchor="w", padx=12)

    list_frame = ttk.Frame(root)
    list_frame.pack(fill="both", expand=True, padx=12, pady=8)
    listbox = tk.Listbox(list_frame, height=10, exportselection=False)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def refresh_list(items: list[GridCandidate]):
        listbox.delete(0, tk.END)
        for item in items:
            listbox.insert(tk.END, item.game_name)
        if items:
            listbox.selection_set(0)
            listbox.see(0)

    refresh_list(current)

    search_frame = ttk.Frame(root)
    search_frame.pack(fill="x", padx=12, pady=(0, 8))
    search_var = tk.StringVar(value=discovered_name)
    search_entry = ttk.Entry(search_frame, textvariable=search_var)
    search_entry.pack(side="left", fill="x", expand=True)

    def do_search(_event=None):
        nonlocal current
        query = search_var.get().strip()
        if not query:
            return
        current = rank_candidates(query, search_fn(query))
        refresh_list(current)

    ttk.Button(search_frame, text="Search", command=do_search).pack(side="left", padx=(8, 0))
    search_entry.bind("<Return>", do_search)

    button_frame = ttk.Frame(root)
    button_frame.pack(fill="x", padx=12, pady=(0, 12))

    def selected_candidate() -> GridCandidate | None:
        selection = listbox.curselection()
        if not selection or not current:
            return current[0] if current else None
        index = selection[0]
        if 0 <= index < len(current):
            return current[index]
        return None

    def use_selected(apply_to_rest=False):
        chosen = selected_candidate()
        if not chosen:
            return
        result["pick"] = GridPick(game_id=chosen.game_id, game_name=chosen.game_name, apply_to_rest=apply_to_rest)
        root.destroy()

    def use_discovered():
        result["pick"] = GridPick(game_id=None, game_name=discovered_name)
        root.destroy()

    def skip():
        result["pick"] = GridPick(skip=True)
        root.destroy()

    ttk.Button(button_frame, text="Use selected", command=use_selected).pack(side="left")
    ttk.Button(button_frame, text="Use selected for the rest", command=lambda: use_selected(True)).pack(side="left", padx=6)
    ttk.Button(button_frame, text="Use discovered name", command=use_discovered).pack(side="left")
    ttk.Button(button_frame, text="Skip", command=skip).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", skip)
    root.attributes("-topmost", True)
    root.after(100, lambda: root.attributes("-topmost", False))
    search_entry.focus_set()
    root.mainloop()
    return result["pick"]
