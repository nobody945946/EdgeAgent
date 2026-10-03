"""人設 page: pick who "你" is and edit the character sheet the LLM follows."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace

from silenttalk import theme
from silenttalk.copilot.persona import (
    RECOMMENDED_PROMPT_CHARS,
    REPLY_LENGTHS,
    TEXT_FIELDS,
    TRAIT_AXES,
    Persona,
    PersonaError,
    PersonaState,
    PersonaStore,
    Relationship,
    blank_persona,
    new_persona_id,
    validate_persona,
)
from silenttalk.voice_styles import (
    PERSONA_VOICES,
    PITCH_STEPS,
    SPEED_STEPS,
    VOICE_EFFECTS,
)
from silenttalk.widgets import (
    Avatar,
    Button,
    Chip,
    Disclosure,
    Field,
    ScrollArea,
    Segmented,
    TraitSlider,
    wrap_to_width,
)

ApplyCallback = Callable[[Persona], None]
StatusCallback = Callable[[str], None]
PreviewCallback = Callable[[Persona, str], None]

NONE_ID = "none"

# (section title, subtitle, fields, open at first). Fields are keys of
# TEXT_FIELDS, or the special "traits" / "reply_length" / "relationships" /
# "voice" editors. Only what reaches the model (Persona.render_prompt_block)
# is here: catchphrases, sample lines, pet peeves, knowledge, secrets and
# boundaries were copied word for word or did nothing for a small model.
# Older personas keep those values in personas.json; they are just not shown.
SECTIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "基本",
        "別人怎麼叫你、你是誰，以及你和頻道裡每個人的關係。",
        ("nickname", "identity", "relationships"),
        True,
    ),
    (
        "他想要什麼",
        "這一段最重要：想要的、怕的、在意的、最近發生的事，決定他會聊什麼、"
        "冷場時會說什麼，也是別人認識他的地方。寫得越具體越像真人。",
        ("desires", "fears", "values", "recent", "background"),
        True,
    ),
    (
        "個性",
        "他講話的感覺。不用寫他常說哪些詞：小模型會每句照抄，聽起來很假。",
        ("traits", "speech_style", "humor_style", "reply_length"),
        False,
    ),
    (
        "聲音",
        "這個角色講話的聲音；Bot 說的每句話都會用它，不影響 AI 想出來的內容。",
        ("voice",),
        False,
    ),
)
MULTILINE = {"background": 3, "sample_lines": 3, "speech_style": 2, "desires": 2}
HINTS = {
    "nickname": "用「、」分隔。語音常被聽錯的寫法也可以寫進來（例：凱哥、開哥）。",
    "recent": "例：昨天排位連輸五場、下個月要搬家。他每天會從這裡想出幾件心事。",
    "speech_style": "例：句子短、講話直接、偶爾一句台語。引號裡的詞不會交給 AI。",
}


class PersonaPanel(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        store: PersonaStore | None,
        *,
        on_apply: ApplyCallback,
        on_status: StatusCallback,
        on_preview: PreviewCallback | None = None,
    ) -> None:
        super().__init__(master, bg=theme.BG)
        self._on_preview = on_preview
        self._voice = ""
        self._voice_chips: dict[str, Chip] = {}
        self._store = store
        self._on_apply = on_apply
        self._on_status = on_status
        self._state = PersonaState([])
        self._editing: str = NONE_ID
        self._dirty = False
        self._loading = False
        self._fields: dict[str, Field] = {}
        self._sliders: dict[str, TraitSlider] = {}
        self._relation_rows: list[tuple[tk.Frame, tk.StringVar, tk.StringVar]] = []

        # ---- left: persona list ----------------------------------------------
        left = tk.Frame(self, bg=theme.SURFACE, width=theme.px(230))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(
            left,
            text="人設",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.font(15, "bold"),
        ).pack(anchor="w", padx=theme.px(18), pady=(theme.px(18), 0))
        tk.Label(
            left,
            text="Bot 扮演或替你說話時的身分",
            bg=theme.SURFACE,
            fg=theme.DIM,
            font=theme.font(9),
        ).pack(anchor="w", padx=theme.px(18), pady=(0, theme.px(12)))
        self._list = tk.Frame(left, bg=theme.SURFACE)
        self._list.pack(fill="x")
        actions = tk.Frame(left, bg=theme.SURFACE)
        actions.pack(fill="x", side="bottom", padx=theme.px(12), pady=theme.px(14))
        Button(actions, "＋ 新人設", self._new, kind="secondary", size=9).pack(
            side="left", fill="x", expand=True
        )
        Button(actions, "複製", self._duplicate, kind="ghost", size=9).pack(
            side="left", padx=(6, 0)
        )

        # ---- right: editor ---------------------------------------------------
        right = tk.Frame(self, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True)

        bar = tk.Frame(right, bg=theme.BG)
        bar.pack(fill="x", padx=theme.px(24), pady=(theme.px(18), theme.px(12)))
        self._avatar = Avatar(bar, 40, bg=theme.BG)
        self._avatar.pack(side="left")
        name_box = tk.Frame(bar, bg=theme.BG)
        name_box.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self._name_var = tk.StringVar()
        self._name_entry = tk.Entry(
            name_box,
            textvariable=self._name_var,
            bg=theme.BG,
            fg=theme.TEXT,
            insertbackground=theme.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=theme.BG,
            highlightcolor=theme.ACCENT,
            font=theme.font(15, "bold"),
            width=14,
        )
        self._name_entry.pack(anchor="w", fill="x")
        # Looks like a title until you hover it; then it shows it is editable.
        self._name_entry.bind(
            "<Enter>", lambda _: self._name_entry.configure(highlightbackground=theme.LINE)
        )
        self._name_entry.bind(
            "<Leave>", lambda _: self._name_entry.configure(highlightbackground=theme.BG)
        )
        self._name_var.trace_add("write", lambda *_: self._changed())
        self._subtitle = tk.Label(
            name_box, text="", bg=theme.BG, fg=theme.DIM, font=theme.font(9), anchor="w"
        )
        self._subtitle.pack(anchor="w")
        buttons = tk.Frame(bar, bg=theme.BG)
        buttons.pack(side="right")
        self._delete_button = Button(
            buttons, "刪除", self._delete, kind="danger", size=9
        )
        self._delete_button.pack(side="left")
        self._save_button = Button(
            buttons, "儲存", self._save_clicked, kind="secondary", size=9
        )
        self._save_button.pack(side="left", padx=(6, 0))
        self._use_button = Button(
            buttons, "使用這個人設", self._use, kind="primary", size=9
        )
        self._use_button.pack(side="left", padx=(6, 0))

        self._scroll = ScrollArea(right)
        self._scroll.pack(fill="both", expand=True, padx=(theme.px(24), 0))
        body = self._scroll.body
        self._blank_note = tk.Label(
            body,
            text=(
                "目前不使用人設：AI 用中性的口吻替你回覆。\n"
                "左邊選一個範本，或按「＋ 新人設」開始設計你的角色。"
            ),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.font(11),
            justify="left",
        )
        self._editor = tk.Frame(body, bg=theme.BG)
        self._sections: list[Disclosure] = []
        for title, subtitle, keys, opened in SECTIONS:
            self._build_section(self._editor, title, subtitle, keys, opened)
        self._build_preview(self._editor)

        self.reload()

    # ---- public ----------------------------------------------------------------

    def reload(self) -> None:
        if self._store is not None:
            try:
                self._state = self._store.load()
            except PersonaError as exc:
                self._on_status(str(exc))
                self._state = PersonaState([])
        self._editing = self._state.active
        self._render_list()
        self._load_editor()

    @property
    def state(self) -> PersonaState:
        return self._state

    def set_mood(self, mood: str) -> None:
        """Called from story mode; mood is global so it saves immediately."""
        self._state.mood = mood
        self._persist()
        self._on_apply(self._state.active_persona())
        self._update_preview()

    def flush(self) -> None:
        """Save pending edits (window hidden / page switched)."""
        if self._dirty:
            self._save(apply=True, quiet=True)

    # ---- building --------------------------------------------------------------

    def _build_section(
        self,
        parent: tk.Frame,
        title: str,
        subtitle: str,
        keys: tuple[str, ...],
        opened: bool,
    ) -> None:
        section = Disclosure(parent, title, subtitle, opened=opened)
        section.pack(fill="x", pady=(0, theme.px(12)), padx=(0, theme.px(18)))
        self._sections.append(section)
        box = section.body
        for key in keys:
            if key == "traits":
                for axis in TRAIT_AXES:
                    slider = TraitSlider(
                        box, axis.low, axis.high, lambda _v: self._changed()
                    )
                    slider.pack(anchor="w", pady=theme.px(3))
                    self._sliders[axis.key] = slider
            elif key == "reply_length":
                row = tk.Frame(box, bg=theme.PANEL)
                row.pack(fill="x", pady=(theme.px(4), theme.px(10)))
                tk.Label(
                    row,
                    text="回覆長度",
                    bg=theme.PANEL,
                    fg=theme.MUTED,
                    font=theme.font(9),
                ).pack(side="left", padx=(0, 10))
                self._length = Segmented(
                    row,
                    [("short", "短"), ("normal", "中"), ("long", "長")],
                    lambda _v: self._changed(),
                )
                self._length.pack(side="left")
                self._length_hint = tk.Label(
                    row, text="", bg=theme.PANEL, fg=theme.DIM, font=theme.font(8)
                )
                self._length_hint.pack(side="left", padx=(10, 0))
            elif key == "voice":
                self._build_voice(box)
            elif key == "relationships":
                tk.Label(
                    box, text="人際關係", bg=theme.PANEL, fg=theme.MUTED, font=theme.font(9)
                ).pack(anchor="w")
                wrap_to_width(
                    tk.Label(
                        box,
                        text=(
                            "左邊填 Discord 顯示名稱（也填你自己），右邊從角色的角度寫這個人是誰。"
                            "例：「阿明」「我老公，結婚三年」→ 角色會把阿明當老公。"
                        ),
                        bg=theme.PANEL,
                        fg=theme.DIM,
                        font=theme.font(9),
                        justify="left",
                        anchor="w",
                    )
                ).pack(anchor="w", fill="x", pady=(0, theme.px(6)))
                self._relations = tk.Frame(box, bg=theme.PANEL)
                self._relations.pack(fill="x")
                Button(
                    box, "＋ 新增一位", self._add_relation_row, kind="ghost", size=9
                ).pack(anchor="w", pady=(theme.px(6), 0))
            else:
                label, _, limit = TEXT_FIELDS[key]
                field = Field(
                    box,
                    label,
                    limit=limit,
                    lines=MULTILINE.get(key, 1),
                    hint=HINTS.get(key, ""),
                    on_change=self._changed,
                )
                field.pack(fill="x", pady=(0, theme.px(10)))
                self._fields[key] = field

    def _build_voice(self, box: tk.Frame) -> None:
        grid = tk.Frame(box, bg=theme.PANEL)
        grid.pack(anchor="w", pady=(0, 8))
        for index, (voice, label) in enumerate(PERSONA_VOICES):
            chip = Chip(grid, label, lambda v=voice: self._pick_voice(v), size=9)
            chip.grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 6), pady=2)
            self._voice_chips[voice] = chip
        self._voice_knobs: dict[str, Segmented] = {}
        for key, label, steps in (
            ("voice_pitch", "音高", [(str(v), t) for v, t in PITCH_STEPS]),
            ("voice_speed", "語速", [(str(v), t) for v, t in SPEED_STEPS]),
            ("voice_effect", "音效", [(k, name) for k, (name, _) in VOICE_EFFECTS.items()]),
        ):
            row = tk.Frame(box, bg=theme.PANEL)
            row.pack(fill="x", pady=(2, 4))
            tk.Label(
                row, text=label, width=4, anchor="w", bg=theme.PANEL, fg=theme.MUTED,
                font=theme.font(9),
            ).pack(side="left", padx=(0, 10))
            knob = Segmented(row, steps, lambda _v: self._changed())
            knob.pack(side="left")
            self._voice_knobs[key] = knob
        actions = tk.Frame(box, bg=theme.PANEL)
        actions.pack(fill="x", pady=(6, 0))
        Button(actions, "▶ 試聽", self._preview_voice, kind="secondary", size=9).pack(
            side="left"
        )
        tk.Label(
            actions,
            text="用代表台詞的第一句，在這台電腦播放，不會送進 Discord。語速頁的整體語速會再疊加。",
            bg=theme.PANEL, fg=theme.DIM, font=theme.font(8),
        ).pack(side="left", padx=(10, 0))

    def _pick_voice(self, voice: str) -> None:
        self._voice = voice
        for key, chip in self._voice_chips.items():
            chip.set_selected(key == voice)
        self._changed()

    def _preview_voice(self) -> None:
        persona = self._current()
        if persona is None or self._on_preview is None:
            return
        draft = self._collect()
        lines = [line.strip() for line in draft.sample_lines.splitlines() if line.strip()]
        text = lines[0] if lines else f"你好，我是{draft.name or '這個角色'}。"
        self._on_status("試聽中…")
        self._on_preview(draft, text)

    def _build_preview(self, parent: tk.Frame) -> None:
        section = Disclosure(
            parent,
            "AI 實際看到的人設",
            "上面寫的東西整理後給模型的樣子，以及長度。",
            bg=theme.SURFACE,
        )
        section.pack(fill="x", pady=(0, theme.px(18)), padx=(0, theme.px(18)))
        box = section.body
        top = tk.Frame(box, bg=theme.SURFACE)
        top.pack(fill="x")
        self._meter_text = tk.Label(
            top, text="", bg=theme.SURFACE, fg=theme.MUTED, font=theme.font(9)
        )
        self._meter_text.pack(side="left")
        self._meter = tk.Canvas(
            box, height=theme.px(6), bg=theme.SURFACE, highlightthickness=0
        )
        self._meter.pack(fill="x", pady=(theme.px(6), theme.px(8)))
        self._preview = tk.Text(
            box,
            height=8,
            wrap="word",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            relief="flat",
            font=theme.font(9),
            takefocus=0,
        )
        self._preview.pack(fill="x")
        note = tk.Label(
            box,
            text=(
                f"建議控制在 {RECOMMENDED_PROMPT_CHARS} 字內：太長的人設會讓本機小模型"
                "開始不照四行格式，也會讓第一次回覆變慢（之後會被快取）。"
            ),
            bg=theme.SURFACE,
            fg=theme.DIM,
            font=theme.font(9),
            justify="left",
            anchor="w",
        )
        wrap_to_width(note).pack(anchor="w", fill="x", pady=(theme.px(6), 0))

    # ---- list --------------------------------------------------------------------

    def _render_list(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        entries = [(NONE_ID, "不使用人設", "中性口吻")] + [
            (p.id, p.name or "（未命名）", p.identity or "—")
            for p in self._state.personas
        ]
        for persona_id, name, subtitle in entries:
            self._list_row(persona_id, name, subtitle)

    def _list_row(self, persona_id: str, name: str, subtitle: str) -> None:
        editing = persona_id == self._editing
        active = persona_id == self._state.active
        bg = theme.PANEL_HI if editing else theme.SURFACE
        row = tk.Frame(self._list, bg=bg, cursor="hand2")
        row.pack(fill="x", padx=8, pady=1)
        marker = tk.Frame(row, bg=theme.ACCENT if editing else bg, width=theme.px(3))
        marker.pack(side="left", fill="y")
        avatar = Avatar(row, 28, bg=bg)
        avatar.set_name("" if persona_id == NONE_ID else name)
        avatar.pack(side="left", padx=(8, 8), pady=7)
        text = tk.Frame(row, bg=bg)
        text.pack(side="left", fill="x", expand=True)
        title = tk.Frame(text, bg=bg)
        title.pack(anchor="w")
        tk.Label(
            title, text=name, bg=bg, fg=theme.TEXT, font=theme.font(10, "bold")
        ).pack(side="left")
        if active:
            tk.Label(
                title,
                text="使用中",
                bg=theme.OK,
                fg=theme.ACCENT_INK,
                font=theme.font(7, "bold"),
                padx=4,
            ).pack(side="left", padx=(6, 0))
        tk.Label(
            text,
            text=_trim(subtitle, 13),
            bg=bg,
            fg=theme.DIM,
            font=theme.font(8),
            anchor="w",
        ).pack(anchor="w")
        for widget in (
            row,
            marker,
            avatar,
            text,
            title,
            *text.winfo_children(),
            *title.winfo_children(),
        ):
            widget.bind("<Button-1>", lambda _, pid=persona_id: self._open(pid))
            widget.bind(
                "<Double-Button-1>", lambda _, pid=persona_id: self._activate(pid)
            )

    # ---- editor ------------------------------------------------------------------

    def _open(self, persona_id: str) -> None:
        if persona_id == self._editing:
            return
        if self._dirty and not self._save(apply=True, quiet=True):
            return
        self._editing = persona_id
        self._render_list()
        self._load_editor()
        self._scroll.scroll_to_top()

    def _current(self) -> Persona | None:
        return self._state.find(self._editing)

    def _load_editor(self) -> None:
        persona = self._current()
        self._loading = True
        try:
            if persona is None:
                self._editing = NONE_ID
                self._editor.pack_forget()
                self._blank_note.pack(anchor="w", pady=20)
                self._name_var.set("不使用人設")
                self._name_entry.configure(
                    state="disabled",
                    disabledbackground=theme.BG,
                    disabledforeground=theme.TEXT,
                )
                self._avatar.set_name("")
                self._delete_button.pack_forget()
                self._save_button.pack_forget()
            else:
                self._blank_note.pack_forget()
                self._editor.pack(fill="x")
                self._name_entry.configure(state="normal")
                self._name_var.set(persona.name)
                self._avatar.set_name(persona.name)
                for key, field in self._fields.items():
                    field.set(str(getattr(persona, key)))
                for axis in TRAIT_AXES:
                    self._sliders[axis.key].set(persona.trait(axis.key))
                self._length.set(persona.reply_length)
                self._pick_voice(persona.voice)
                self._voice_knobs["voice_pitch"].set(str(_nearest(persona.voice_pitch, PITCH_STEPS)))
                self._voice_knobs["voice_speed"].set(str(_nearest(persona.voice_speed, SPEED_STEPS)))
                self._voice_knobs["voice_effect"].set(persona.voice_effect)
                for frame, _, _ in self._relation_rows:
                    frame.destroy()
                self._relation_rows.clear()
                for rel in persona.relationships:
                    self._add_relation_row(rel)
                if not persona.relationships:
                    self._add_relation_row()
                self._delete_button.pack(side="left")
                self._save_button.pack(side="left", padx=(6, 0))
                self._use_button.pack_forget()
                self._use_button.pack(side="left", padx=(6, 0))
        finally:
            self._loading = False
        self._dirty = False
        self._update_chrome()
        self._update_preview()

    def _add_relation_row(self, rel: Relationship | None = None) -> None:
        frame = tk.Frame(self._relations, bg=theme.PANEL)
        frame.pack(fill="x", pady=2)
        name_var = tk.StringVar(value=rel.name if rel else "")
        relation_var = tk.StringVar(value=rel.relation if rel else "")
        for var, width, placeholder_width in ((name_var, 10, 0), (relation_var, 40, 1)):
            border = tk.Frame(frame, bg=theme.LINE, padx=1, pady=1)
            border.pack(
                side="left", padx=(0, 6), fill="x", expand=bool(placeholder_width)
            )
            entry = tk.Entry(
                border,
                textvariable=var,
                width=width,
                bg=theme.PANEL_HI,
                fg=theme.TEXT,
                insertbackground=theme.TEXT,
                relief="flat",
                highlightthickness=0,
                font=theme.font(10),
            )
            entry.pack(fill="x", ipady=theme.px(5), ipadx=theme.px(6))
            var.trace_add("write", lambda *_: self._changed())
        remove = tk.Label(
            frame,
            text="✕",
            bg=theme.PANEL,
            fg=theme.DIM,
            cursor="hand2",
            font=theme.font(10),
        )
        remove.pack(side="left")
        entry_row = (frame, name_var, relation_var)
        remove.bind("<Button-1>", lambda _: self._remove_relation_row(entry_row))
        self._relation_rows.append(entry_row)
        if rel is None and not self._loading:
            self._changed()

    def _remove_relation_row(
        self, row: tuple[tk.Frame, tk.StringVar, tk.StringVar]
    ) -> None:
        row[0].destroy()
        self._relation_rows.remove(row)
        self._changed()

    def _collect(self) -> Persona:
        base = self._current()
        assert base is not None
        values: dict[str, object] = {
            key: field.get() for key, field in self._fields.items()
        }
        return replace(
            base,
            name=self._name_var.get(),
            traits={axis.key: self._sliders[axis.key].get() for axis in TRAIT_AXES},
            reply_length=self._length.get(),
            voice=self._voice,
            voice_pitch=int(self._voice_knobs["voice_pitch"].get() or 0),
            voice_speed=int(self._voice_knobs["voice_speed"].get() or 0),
            voice_effect=self._voice_knobs["voice_effect"].get(),
            relationships=tuple(
                Relationship(name.get(), relation.get())
                for _, name, relation in self._relation_rows
            ),
            **values,  # type: ignore[arg-type]
        )

    def _changed(self) -> None:
        if self._loading or self._current() is None:
            return
        self._dirty = True
        self._avatar.set_name(self._name_var.get().strip())
        self._update_chrome()
        self._update_preview()

    def _update_chrome(self) -> None:
        persona = self._current()
        if persona is None:
            active = self._state.active == NONE_ID
            self._subtitle.configure(
                text="使用中" if active else "AI 用中性口吻",
                fg=theme.OK if active else theme.DIM,
            )
            self._use_button.configure(text="已在使用" if active else "改回不使用人設")
            self._use_button.set_enabled(not active)
            return
        active = persona.id == self._state.active
        parts = ["● 使用中" if active else "未使用"]
        if self._dirty:
            parts.append("未儲存的修改")
        self._subtitle.configure(
            text="　".join(parts),
            fg=theme.WARN if self._dirty else (theme.OK if active else theme.DIM),
        )
        self._use_button.configure(
            text="已在使用" if active and not self._dirty else "使用這個人設"
        )
        self._use_button.set_enabled(self._dirty or not active)
        self._save_button.set_enabled(self._dirty)
        self._length_hint.configure(text=REPLY_LENGTHS.get(self._length.get(), ""))

    def _update_preview(self) -> None:
        if self._current() is None:
            return
        try:
            persona = replace(self._collect(), mood=self._state.mood)
        except (TypeError, ValueError):
            return
        sheet = persona.render_prompt_block()
        notes = [
            f"{r.name}：{r.relation}"
            for r in persona.relationships
            if r.name and r.relation
        ]
        shown = sheet or "（空白：AI 會用中性口吻）"
        if notes:
            shown += "\n\n〔對話中出現才加入〕\n" + "\n".join(notes)
        self._preview.configure(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", shown)
        self._preview.configure(
            state="disabled", height=min(14, max(4, shown.count("\n") + 2))
        )
        length = len(sheet)
        ratio = min(1.0, length / RECOMMENDED_PROMPT_CHARS)
        color = (
            theme.OK
            if ratio < 0.8
            else (theme.WARN if length <= RECOMMENDED_PROMPT_CHARS else theme.ERROR)
        )
        self._meter_text.configure(
            text=f"{length} / {RECOMMENDED_PROMPT_CHARS} 字", fg=color
        )
        self._meter.update_idletasks()
        width = max(1, self._meter.winfo_width())
        self._meter.delete("all")
        self._meter.create_rectangle(0, 0, width, 6, fill=theme.PANEL_TOP, outline="")
        self._meter.create_rectangle(0, 0, width * ratio, 6, fill=color, outline="")

    # ---- actions -----------------------------------------------------------------

    def _save(self, *, apply: bool, quiet: bool = False) -> bool:
        persona = self._current()
        if persona is None:
            return True
        try:
            updated = validate_persona(self._collect())
        except PersonaError as exc:
            self._on_status(str(exc))
            return False
        self._state.personas = [
            updated if p.id == updated.id else p for p in self._state.personas
        ]
        if not self._persist():
            return False
        self._dirty = False
        self._render_list()
        self._update_chrome()
        if apply and updated.id == self._state.active:
            self._on_apply(self._state.active_persona())
        if not quiet:
            self._on_status(f"已儲存人設「{updated.name or '未命名'}」")
        return True

    def _save_clicked(self) -> None:
        self._save(apply=True)

    def _use(self) -> None:
        if self._current() is not None and not self._save(apply=False, quiet=True):
            return
        self._activate(self._editing)

    def _activate(self, persona_id: str) -> None:
        if self._dirty and not self._save(apply=False, quiet=True):
            return
        self._state.active = persona_id
        self._persist()
        self._editing = persona_id
        self._render_list()
        self._load_editor()
        persona = self._state.active_persona()
        self._on_apply(persona)
        self._on_status(
            f"現在以「{persona.name}」的身分回覆"
            if persona.name
            else "已改回不使用人設"
        )

    def _new(self) -> None:
        self._add(replace(blank_persona(new_persona_id()), name="新人設"))

    def _duplicate(self) -> None:
        source = self._current()
        if source is None:
            self._new()
            return
        if self._dirty and not self._save(apply=True, quiet=True):
            return
        self._add(
            replace(source, id=new_persona_id(), name=_trim(source.name + " 副本", 24))
        )

    def _add(self, persona: Persona) -> None:
        if self._dirty and not self._save(apply=True, quiet=True):
            return
        if (
            self._store is not None
            and len(self._state.personas) >= self._store.max_personas
        ):
            self._on_status(f"人設最多 {self._store.max_personas} 個")
            return
        self._state.personas.append(persona)
        self._persist()
        self._editing = persona.id
        self._render_list()
        self._load_editor()
        self._scroll.scroll_to_top()
        self._name_entry.focus_set()
        self._name_entry.select_range(0, "end")

    def _delete(self) -> None:
        persona = self._current()
        if persona is None:
            return
        if not getattr(self, "_confirm_delete", False):
            self._confirm_delete = True
            self._delete_button.configure(text="再按一次確認刪除")
            self.after(3000, self._reset_delete)
            return
        self._reset_delete()
        self._state.personas = [p for p in self._state.personas if p.id != persona.id]
        was_active = self._state.active == persona.id
        if was_active:
            self._state.active = NONE_ID
        self._persist()
        self._dirty = False
        self._editing = NONE_ID
        self._render_list()
        self._load_editor()
        if was_active:
            self._on_apply(self._state.active_persona())
        self._on_status(f"已刪除人設「{persona.name or '未命名'}」")

    def _reset_delete(self) -> None:
        self._confirm_delete = False
        self._delete_button.configure(text="刪除")

    def _persist(self) -> bool:
        if self._store is None:
            return True
        try:
            self._store.save(self._state)
        except PersonaError as exc:
            self._on_status(str(exc))
            return False
        return True


def _trim(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _nearest(value: int, steps: tuple[tuple[int, str], ...]) -> int:
    """Hand-edited personas.json may hold any value; show the closest step."""
    return min((step for step, _ in steps), key=lambda step: abs(step - value))
