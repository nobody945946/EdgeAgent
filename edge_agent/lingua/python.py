"""Python code to plain Traditional Chinese, exactly.

Python's own parser (ast) splits the code; each statement and expression is
told with fixed wording. The behaviours people get wrong are said where they
apply: / always gives a float, // and % follow floor division (-7 // 2 is -4,
-7 % 3 is 2), round() rounds halves to even (round(2.5) is 2), b = a shares
the object instead of copying it, a mutable default argument is created once
and shared, list.sort() returns None, open(..., "w") empties the file and,
without encoding=, uses the system's encoding (cp950 on Traditional Chinese
Windows with Python 3.12). tests/test_lingua_python.py runs every such claim.

Types are not known before the code runs. Where a method's meaning depends
on the object (x.split, x.append), the sentence says which kind of object it
assumes.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

COMPOUND = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.AsyncFor, ast.While,
            ast.Try, ast.With, ast.AsyncWith, ast.Match)
BUILTINS = {
    "print": "把內容顯示出來（輸出到畫面），多個值之間預設用一個空格隔開，結尾換行",
    "len": "數一數有幾個（字串是幾個字，清單是幾項）",
    "int": "轉成整數（小數會直接砍掉小數部分，往 0 的方向：int(-2.7) 是 -2；文字要是整數的樣子）",
    "float": "轉成浮點數（小數）",
    "str": "轉成文字",
    "bool": "轉成 True／False（0、空字串、空清單、None 都是 False）",
    "list": "做成一個新的清單",
    "dict": "做成一個新的字典",
    "set": "做成一個新的集合（去掉重複，沒有順序）",
    "tuple": "做成一個新的 tuple（不能修改的序列）",
    "sum": "加總",
    "min": "最小值",
    "max": "最大值",
    "abs": "絕對值",
    "round": "四捨五入；剛好一半時取偶數（round(2.5) 是 2，round(3.5) 是 4）",
    "sorted": "排序後的新清單（原本的不變）",
    "reversed": "倒過來的順序（只能依序走過一次）",
    "enumerate": "依序取出每一項，同時附上編號（預設從 0 開始）",
    "zip": "把幾個序列一項一項配對；長度不同時，到最短的那個結束就停（多出來的直接丟掉）",
    "range": "一串整數",
    "open": "打開檔案",
    "input": "讓使用者輸入一行文字（拿到的一定是文字，要數字得自己轉）",
    "isinstance": "檢查是不是某種型別",
    "any": "任何一個為真就是 True（空的是 False）",
    "all": "全部為真才是 True（空的是 True）",
    "map": "對每一項套用函式（只能依序走過一次）",
    "filter": "只留下讓函式為真的項目（只能依序走過一次）",
    "divmod": "同時算出整除的商和餘數",
    "type": "取得型別",
    "id": "取得物件的身分編號",
    "hasattr": "檢查有沒有某個屬性",
    "getattr": "取得某個屬性",
    "iter": "取得迭代器",
    "next": "取出迭代器的下一項（沒有了就出錯，除非給了預設值）",
}
# Methods, with the kind of object the sentence assumes.
METHODS = {
    "append": ("清單", "把一項加到清單結尾（直接改原本的清單，回傳 None）"),
    "extend": ("清單", "把另一個序列的每一項加到清單結尾（直接改原本的清單）"),
    "insert": ("清單", "把一項插到指定位置（直接改原本的清單）"),
    "pop": ("清單或字典", "取出並移除一項（清單沒給位置時是最後一項）"),
    "remove": ("清單", "移除第一個等於這個值的項目（找不到會出錯）"),
    "sort": ("清單", "直接把原本的清單排好（回傳 None，不是排好的清單）"),
    "split": ("字串", "切開成清單"),
    "strip": ("字串", "去掉前後的空白（不改原字串，得到新字串）"),
    "lstrip": ("字串", "去掉開頭的空白（得到新字串）"),
    "rstrip": ("字串", "去掉結尾的空白（得到新字串）"),
    "replace": ("字串", "把某段文字全部換掉（不改原字串，得到新字串）"),
    "join": ("字串", "用這個字串當分隔，把序列裡的每段文字接起來"),
    "lower": ("字串", "轉小寫（得到新字串）"),
    "upper": ("字串", "轉大寫（得到新字串）"),
    "startswith": ("字串", "是不是以某段文字開頭"),
    "endswith": ("字串", "是不是以某段文字結尾"),
    "format": ("字串", "把值填進字串裡的 {}"),
    "get": ("字典", "取出鍵對應的值；沒有這個鍵時得到預設值（沒給就是 None），不會出錯"),
    "keys": ("字典", "所有的鍵"),
    "values": ("字典", "所有的值"),
    "items": ("字典", "所有的（鍵, 值）配對"),
    "update": ("字典", "用另一個字典的內容更新（同名的鍵會被覆蓋）"),
    "read": ("檔案", "讀出全部內容"),
    "readlines": ("檔案", "讀出每一行，做成清單（每行結尾的換行還在）"),
    "write": ("檔案", "寫入文字（不會自動換行）"),
    "close": ("檔案", "關閉檔案"),
}
OPERATORS = {
    ast.Add: "加（文字和清單是接起來）", ast.Sub: "減", ast.Mult: "乘（文字乘整數是重複）",
    ast.Div: "除；結果一定是浮點數（4/2 是 2.0）",
    ast.FloorDiv: "整除：往下取整數（7 // 2 是 3，-7 // 2 是 -4）",
    ast.Mod: "餘數；正負號跟著除數（-7 % 3 是 2）", ast.Pow: "次方",
    ast.MatMult: "矩陣乘法", ast.BitAnd: "位元 AND（布林值時是「而且」但兩邊都會算）",
    ast.BitOr: "位元 OR（字典時是合併）", ast.BitXor: "位元 XOR", ast.LShift: "位元左移", ast.RShift: "位元右移",
}
COMPARE = {
    ast.Eq: "等於", ast.NotEq: "不等於", ast.Lt: "小於", ast.LtE: "小於或等於", ast.Gt: "大於",
    ast.GtE: "大於或等於", ast.Is: "是同一個物件（is 不是比值；比值要用 ==）", ast.IsNot: "不是同一個物件",
    ast.In: "在裡面", ast.NotIn: "不在裡面",
}
OPEN_MODES = {
    "r": "讀取（檔案不存在會出錯）", "w": "寫入：檔案已存在時會先被清空，不存在就建立",
    "a": "附加：寫的內容加在檔案結尾，不存在就建立", "x": "建立新檔寫入：檔案已存在就出錯",
}


@dataclass
class Line:
    depth: int
    source: str
    text: str


@dataclass
class Translation:
    code: str
    lines: list[Line]
    error: str = ""

    def text(self) -> str:
        if self.error:
            return f"Python 程式碼：\n{self.code}\n{self.error}"
        body, counters = [], [0] * 64
        for line in self.lines:
            counters[line.depth] += 1
            for deeper in range(line.depth + 1, len(counters)):
                counters[deeper] = 0
            number = ".".join(str(n) for n in counters[:line.depth + 1])
            source = f"`{line.source}`：" if line.source else ""
            body.append(f"{'  ' * line.depth}{number}. {source}{line.text}")
        return "\n".join(["Python 程式碼（依 Python 3.12 的規則）", "逐項說明："] + body)


class Writer:
    def __init__(self, code: str) -> None:
        self.code = code
        self.lines: list[Line] = []

    def src(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        text = ast.get_source_segment(self.code, node) or ""
        first = text.split("\n", 1)[0]
        return first if len(first) <= 120 else first[:117] + "…"

    def add(self, depth: int, node: ast.AST | None, text: str) -> None:
        self.lines.append(Line(depth, self.src(node), text))

    # -------------------------------------------------------- statements

    def body(self, statements: list[ast.stmt], depth: int) -> None:
        for statement in statements:
            self.statement(statement, depth)

    def statement(self, node: ast.stmt, depth: int) -> None:
        before = len(self.lines)
        self.statement_line(node, depth)
        if before < len(self.lines) and not isinstance(node, COMPOUND):
            self.inner(node, self.lines[before].depth + 1)

    def inner(self, node: ast.stmt, depth: int) -> None:
        """Calls and operators inside a simple statement whose behaviour is
        easy to get wrong, each with its own line."""
        top = getattr(node, "value", None)
        seen = set()
        for sub in ast.walk(node):
            if sub is top or sub is node:
                continue
            text = None
            if isinstance(sub, ast.Call) and (
                    (isinstance(sub.func, ast.Name) and sub.func.id in BUILTINS)
                    or (isinstance(sub.func, ast.Attribute) and sub.func.attr in METHODS)):
                text = self.call(sub)
            elif isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                text = self.expr(sub)
            if text and self.src(sub) not in seen:
                seen.add(self.src(sub))
                self.add(depth, sub, text.split("：", 1)[-1] if text.startswith(self.src(sub) + "：") else text)

    def statement_line(self, node: ast.stmt, depth: int) -> None:
        if isinstance(node, ast.Import):
            names = "、".join(a.name + (f"（之後叫它 {a.asname}）" if a.asname else "") for a in node.names)
            self.add(depth, node, f"載入模組 {names}")
        elif isinstance(node, ast.ImportFrom):
            names = "、".join(a.name + (f"（之後叫它 {a.asname}）" if a.asname else "") for a in node.names)
            self.add(depth, node, f"從模組 {'.' * node.level}{node.module or ''} 載入 {names}")
        elif isinstance(node, ast.Assign):
            self.assign(node, depth)
        elif isinstance(node, ast.AugAssign):
            op = OPERATORS.get(type(node.op), type(node.op).__name__)
            extra = f"；如果 {self.src(node.target)} 是清單，+= 會直接改原本的清單（其他指向它的名字也會看到）" if isinstance(node.op, ast.Add) else ""
            self.add(depth, node, f"把 {self.src(node.target)} 和 {self.src(node.value)} 做「{op}」，結果存回 {self.src(node.target)}{extra}")
        elif isinstance(node, ast.AnnAssign):
            value = f"，值是：{self.expr(node.value)}" if node.value is not None else "（只宣告，沒有給值）"
            self.add(depth, node, f"{self.src(node.target)} 標註為 {self.src(node.annotation)} 型別（只是說明，Python 執行時不檢查）{value}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.function(node, depth)
        elif isinstance(node, ast.ClassDef):
            bases = f"，繼承自 {'、'.join(self.src(b) for b in node.bases)}" if node.bases else ""
            self.add(depth, None, f"定義類別 {node.name}{bases}：")
            self.body(node.body, depth + 1)
        elif isinstance(node, ast.Return):
            value = self.expr(node.value) if node.value is not None else "None（什麼都不回傳）"
            self.add(depth, node, f"結束這個函式，回傳：{value}")
        elif isinstance(node, ast.If):
            self.if_chain(node, depth)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.add(depth, None, f"`for {self.src(node.target)} in {self.src(node.iter)}`：對 {self.expr(node.iter)} 的每一項，"
                                  f"依序放進 {self.src(node.target)}，執行：")
            self.body(node.body, depth + 1)
            if node.orelse:
                self.add(depth, None, "迴圈正常走完（沒有被 break 中斷）時，再執行：")
                self.body(node.orelse, depth + 1)
        elif isinstance(node, ast.While):
            self.add(depth, None, f"`while {self.src(node.test)}`：只要 {self.condition(node.test)}，就重複執行：")
            self.body(node.body, depth + 1)
            if node.orelse:
                self.add(depth, None, "條件不成立而結束（沒有被 break 中斷）時，再執行：")
                self.body(node.orelse, depth + 1)
        elif isinstance(node, ast.Break):
            self.add(depth, node, "立刻跳出最內層的迴圈")
        elif isinstance(node, ast.Continue):
            self.add(depth, node, "跳過這一輪剩下的部分，直接進下一輪")
        elif isinstance(node, ast.Pass):
            self.add(depth, node, "什麼都不做（佔位）")
        elif isinstance(node, ast.Try):
            self.try_(node, depth)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                target = f"，叫它 {self.src(item.optional_vars)}" if item.optional_vars is not None else ""
                self.add(depth, item.context_expr, f"{self.expr(item.context_expr)}{target}；"
                                                   "下面這段執行完（包括出錯時）會自動收尾（檔案會自動關閉）：")
            self.body(node.body, depth + 1)
        elif isinstance(node, ast.Raise):
            what = self.expr(node.exc) if node.exc is not None else "剛才接到的同一個錯誤"
            self.add(depth, node, f"丟出錯誤：{what}（這一段停止，交給外層處理）")
        elif isinstance(node, ast.Assert):
            self.add(depth, node, f"檢查 {self.condition(node.test)}，不成立就丟出 AssertionError"
                                  "（注意：用 python -O 執行時這行會被整個略過）")
        elif isinstance(node, ast.Delete):
            self.add(depth, node, "刪除 " + "、".join(self.src(t) for t in node.targets)
                     + "（刪的是名字或項目；物件沒人用了才會被回收）")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            scope = "全域" if isinstance(node, ast.Global) else "外層函式"
            self.add(depth, node, f"{'、'.join(node.names)} 用的是{scope}的那個變數，不是新的區域變數")
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.add(depth, None, "說明文字（docstring 或字串），不影響執行")
            else:
                self.add(depth, node, self.expr(node.value))
        elif isinstance(node, ast.Match):
            self.add(depth, None, f"`match {self.src(node.subject)}`：依 {self.src(node.subject)} 的樣子，從上到下找第一個符合的情況：")
            for case in node.cases:
                self.add(depth + 1, None, f"情況 `{self.src(case.pattern)}`" + (f"（而且 {self.src(case.guard)}）" if case.guard else "") + "：")
                self.body(case.body, depth + 2)
        else:
            self.add(depth, node, f"{type(node).__name__} 敘述（沒有中文說明）")

    def assign(self, node: ast.Assign, depth: int) -> None:
        value = node.value
        if len(node.targets) > 1 and all(isinstance(t, ast.Name) for t in node.targets):
            names = "、".join(t.id for t in node.targets)
            self.add(depth, node, f"{names} 都指向同一個物件：{self.expr(value)}（不是各一份；如果是清單、字典這類可修改的物件，"
                                  "透過任一個名字修改，其他名字也會看到）")
            return
        for target in node.targets:
            name = self.src(target)
            if isinstance(target, (ast.Tuple, ast.List)):
                self.add(depth, node, f"把 {self.expr(value)} 依序拆開，分別存進 {'、'.join(self.src(e) for e in target.elts)}"
                                      "（個數不合會出錯）")
            elif isinstance(value, ast.Name):
                self.add(depth, node, f"讓 {name} 指向 {value.id} 同一個物件（不是複製；如果是清單、字典這類可修改的物件，"
                                      f"透過任一個名字修改，另一個也會看到）")
            elif isinstance(target, ast.Subscript):
                self.add(depth, node, f"把 {self.src(target.value)} 裡 {self.src(target.slice)} 這一項設成：{self.expr(value)}")
            elif isinstance(target, ast.Attribute):
                self.add(depth, node, f"把 {self.src(target.value)} 的屬性 {target.attr} 設成：{self.expr(value)}")
            else:
                self.add(depth, node, f"{name} 存放：{self.expr(value)}")

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, depth: int) -> None:
        args = node.args
        params = []
        positional = args.posonlyargs + args.args
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        mutable = []
        for arg, default in zip(positional, defaults):
            text = arg.arg
            if default is not None:
                text += f"（沒給時預設 {self.src(default)}）"
                if isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(default, ast.Call) and self.src(default.func) in ("list", "dict", "set")):
                    mutable.append(arg.arg)
            params.append(text)
        if args.vararg:
            params.append(f"*{args.vararg.arg}（其餘的位置參數，收成 tuple）")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            params.append(arg.arg + "（只能用名稱傳入" + (f"，預設 {self.src(default)}" if default is not None else "") + "）")
        if args.kwarg:
            params.append(f"**{args.kwarg.arg}（其餘的具名參數，收成字典）")
        kind = "非同步函式" if isinstance(node, ast.AsyncFunctionDef) else "函式"
        decorators = f"（套上 {'、'.join('@' + self.src(d) for d in node.decorator_list)}）" if node.decorator_list else ""
        self.add(depth, None, f"定義{kind} {node.name}{decorators}，參數：{'、'.join(params) or '沒有'}；被呼叫時執行：")
        for name in mutable:
            self.add(depth + 1, None, f"⚠ 參數 {name} 的預設值是可修改的物件：它只在定義函式時建立一次，"
                                      "之後每次沒給這個參數的呼叫都共用同一個，改了會留到下一次")
        self.body(node.body, depth + 1)

    def if_chain(self, node: ast.If, depth: int, first: bool = True) -> None:
        word = "如果" if first else "否則如果"
        self.add(depth, None, f"`{'if' if first else 'elif'} {self.src(node.test)}`：{word} {self.condition(node.test)}，執行：")
        self.body(node.body, depth + 1)
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self.if_chain(node.orelse[0], depth, first=False)
        elif node.orelse:
            self.add(depth, None, "`else`：以上都不成立時，執行：")
            self.body(node.orelse, depth + 1)

    def try_(self, node: ast.Try, depth: int) -> None:
        self.add(depth, None, "`try`：試著執行下面這段：")
        self.body(node.body, depth + 1)
        for handler in node.handlers:
            if handler.type is None:
                what = "任何錯誤（⚠ 沒寫錯誤種類：連 Ctrl+C 中斷都會被攔下）"
            elif self.src(handler.type) in ("Exception", "BaseException"):
                what = f"幾乎所有錯誤（{self.src(handler.type)}，⚠ 會把意料之外的錯誤也吞掉）"
            else:
                what = f"{self.src(handler.type)} 這種錯誤"
            name = f"，錯誤叫做 {handler.name}" if handler.name else ""
            self.add(depth, None, f"`except`：如果發生{what}{name}，改執行：")
            self.body(handler.body, depth + 1)
        if node.orelse:
            self.add(depth, None, "`else`：如果完全沒出錯，接著執行：")
            self.body(node.orelse, depth + 1)
        if node.finalbody:
            self.add(depth, None, "`finally`：不管有沒有出錯，最後一定執行：")
            self.body(node.finalbody, depth + 1)

    # -------------------------------------------------------- expressions

    def condition(self, node: ast.expr) -> str:
        if isinstance(node, ast.BoolOp):
            word = " 而且 " if isinstance(node.op, ast.And) else " 或者 "
            return word.join(self.condition(v) for v in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"不是（{self.condition(node.operand)}）"
        if isinstance(node, ast.Compare):
            return self.expr(node)
        return f"{self.src(node)} 為真（0、空字串、空清單、None 都算假）"

    def expr(self, node: ast.expr | None) -> str:
        if node is None:
            return "None"
        src = self.src(node)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, str):
                return f"文字「{value}」"
            if value is None:
                return "None（沒有值）"
            if isinstance(value, bool):
                return str(value)
            return f"數值 {src}"
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.JoinedStr):
            return f"文字 {src}（大括號裡的運算結果會填進去）"
        if isinstance(node, ast.BinOp):
            op = OPERATORS.get(type(node.op), type(node.op).__name__)
            return f"{self.src(node.left)} 和 {self.src(node.right)} 做「{op}」"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"不是（{self.condition(node.operand)}）"
            return f"{src}"
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return ("、而且".join(self.src(v) for v in node.values)
                        + "（and：從左算起，遇到第一個為假的就停，結果是那個值本身；全都為真時是最後一個值）")
            return ("、或者".join(self.src(v) for v in node.values)
                    + "（or：從左算起，遇到第一個為真的就停，結果是那個值本身；全都為假時是最後一個值）")
        if isinstance(node, ast.Compare):
            parts = [self.src(node.left)]
            for op, right in zip(node.ops, node.comparators):
                parts.append(f"{COMPARE.get(type(op), type(op).__name__)} {self.src(right)}")
            chained = "（連續比較：每一段都成立才算）" if len(node.ops) > 1 else ""
            return " ".join(parts) + chained
        if isinstance(node, ast.Subscript):
            return self.subscript(node)
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.Attribute):
            return f"{self.src(node.value)} 的屬性 {node.attr}"
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            kind = {ast.ListComp: "新清單", ast.SetComp: "新集合（去掉重複）",
                    ast.GeneratorExp: "產生器（用到時才一項項算，只能走過一次）", ast.DictComp: "新字典"}[type(node)]
            loops = []
            for gen in node.generators:
                text = f"對 {self.src(gen.iter)} 的每一項 {self.src(gen.target)}"
                if gen.ifs:
                    text += "，只留下 " + "、而且 ".join(self.src(i) for i in gen.ifs) + " 的"
                loops.append(text)
            item = (f"{self.src(node.key)}: {self.src(node.value)}" if isinstance(node, ast.DictComp)
                    else self.src(node.elt))
            return f"{kind}：{'；'.join(loops)}，算出 {item}"
        if isinstance(node, ast.IfExp):
            return f"如果 {self.src(node.test)} 就是 {self.src(node.body)}，否則是 {self.src(node.orelse)}"
        if isinstance(node, ast.Lambda):
            return f"一個沒有名字的小函式：輸入 {self.src(node.args) or '（沒有參數）'}，算出 {self.src(node.body)}"
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            kind = {ast.List: "清單", ast.Tuple: "tuple", ast.Set: "集合"}[type(node)]
            return f"{kind} {src}（{len(node.elts)} 項）"
        if isinstance(node, ast.Dict):
            return f"字典 {src}（{len(node.keys)} 組鍵值）"
        if isinstance(node, ast.Await):
            return f"等待 {self.src(node.value)} 完成"
        return f"{src}（{type(node).__name__}，沒有中文說明）"

    def subscript(self, node: ast.Subscript) -> str:
        base = self.src(node.value)
        index = node.slice
        if isinstance(index, ast.Slice):
            lower = self.src(index.lower) if index.lower is not None else "開頭"
            upper = f"第 {self.src(index.upper)} 個之前（不含第 {self.src(index.upper)} 個）" if index.upper is not None else "結尾"
            step = f"，每 {self.src(index.step)} 個取一個" if index.step is not None else ""
            start = f"第 {lower} 個" if index.lower is not None else "開頭"
            return f"{base} 從{start}取到{upper}{step}（編號從 0 起算；得到新的序列）"
        if isinstance(index, ast.UnaryOp) and isinstance(index.op, ast.USub) and isinstance(index.operand, ast.Constant):
            return f"{base} 的倒數第 {index.operand.value} 個"
        if isinstance(index, ast.Constant) and isinstance(index.value, int):
            return f"{base} 的第 {index.value} 個（編號從 0 起算，所以是第 {index.value + 1} 項）"
        return f"{base} 裡 {self.src(index)} 對應的那一項"

    def call(self, node: ast.Call) -> str:
        func = node.func
        args = [self.src(a) for a in node.args]
        kwargs = {k.arg: self.src(k.value) for k in node.keywords if k.arg}
        if isinstance(func, ast.Name) and func.id in BUILTINS:
            name = func.id
            if name == "range":
                return self.range_text(node)
            if name == "open":
                return self.open_text(node)
            if name == "print":
                sep = f"，分隔改成 {kwargs['sep']}" if "sep" in kwargs else ""
                end = f"，結尾改成 {kwargs['end']}" if "end" in kwargs else ""
                return f"把 {'、'.join(args) or '（空行）'} 顯示出來（多個值之間預設用一個空格隔開，結尾換行{sep}{end}）"
            if name == "round" and len(node.args) == 1:
                return f"把 {args[0]} 四捨五入成整數；剛好一半時取偶數（round(2.5) 是 2，round(3.5) 是 4）"
            if name == "round":
                return (f"把 {args[0]} 四捨五入到小數第 {args[1]} 位；剛好一半時取偶數，而且小數在電腦裡有微小誤差，"
                        "有些看似剛好一半的值會往下（round(2.675, 2) 是 2.67）")
            if name == "sorted":
                order = "，由大到小" if kwargs.get("reverse") == "True" else ""
                key = f"，依 {kwargs['key']} 的結果比較" if "key" in kwargs else ""
                return f"把 {args[0] if args else ''} 排序後做成新清單{order}{key}（原本的不變）"
            return f"{name}({', '.join(args + [f'{k}={v}' for k, v in kwargs.items()])})：{BUILTINS[name]}"
        if isinstance(func, ast.Attribute) and func.attr in METHODS:
            kind, text = METHODS[func.attr]
            owner = self.src(func.value)
            if func.attr == "split":
                if not node.args and "sep" not in kwargs:
                    text = "依空白切開成清單（連續的空白算一個，前後的空白忽略）"
                else:
                    text = f"依 {args[0] if args else kwargs['sep']} 切開成清單（每個分隔都會切，連續的分隔之間會有空字串）"
            if func.attr == "sort" and kwargs.get("reverse") == "True":
                text += "，由大到小"
            joined = ", ".join(args + [f"{k}={v}" for k, v in kwargs.items()])
            return f"{owner}.{func.attr}({joined})：{text}（假設 {owner} 是{kind}）"
        joined = ", ".join(args + [f"{k}={v}" for k, v in kwargs.items()])
        return f"呼叫 {self.src(func)}({joined})（這個函式的作用要看它的定義，這裡不猜）"

    def range_text(self, node: ast.Call) -> str:
        args = [self.src(a) for a in node.args]
        if len(args) == 1:
            return f"range({args[0]})：從 0 到 {args[0]} 的前一個整數（不含 {args[0]}），共 {args[0]} 個"
        if len(args) == 2:
            return f"range({args[0]}, {args[1]})：從 {args[0]} 到 {args[1]} 的前一個整數（不含 {args[1]}）"
        if len(args) == 3:
            return f"range({', '.join(args)})：從 {args[0]} 開始，每次加 {args[2]}，到 {args[1]} 之前停（不含 {args[1]}）"
        return "range(…)"

    def open_text(self, node: ast.Call) -> str:
        args = node.args
        path = self.src(args[0]) if args else "?"
        mode_node = args[1] if len(args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
        mode = mode_node.value if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) else (
            "r" if mode_node is None else None)
        if mode is None:
            what = f"打開檔案 {path}，模式由 {self.src(mode_node)} 決定"
        else:
            base = next((m for m in mode if m in OPEN_MODES), "r")
            what = f"打開檔案 {path} 來{OPEN_MODES[base]}"
            if "+" in mode:
                what += "，同時可讀可寫"
            if "b" in mode:
                what += "；二進位模式（讀寫的是位元組，不是文字）"
        if (mode is None or "b" not in mode) and not any(k.arg == "encoding" for k in node.keywords):
            what += ("；⚠ 沒寫 encoding：用系統的預設編碼讀寫（Python 3.12 在繁體中文 Windows 上是 cp950，"
                     "不是 UTF-8），UTF-8 檔案的中文可能讀錯或寫不進去")
        return what


def translate(code: str) -> Translation:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        where = f"第 {exc.lineno} 行第 {exc.offset} 個字元" if exc.lineno else ""
        return Translation(code, [], f"Python 會拒絕執行這段程式（語法錯誤，{where}）：{exc.msg}")
    writer = Writer(code)
    writer.body(tree.body, 0)
    return Translation(code, writer.lines)
