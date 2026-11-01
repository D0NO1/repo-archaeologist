#!/usr/bin/env python3
"""
Repo Archaeologist — excavate a legacy codebase into an interactive HTML report.

Zero dependencies: requires only Python 3.8+ and a git checkout.

Usage:
    python analyze.py <repo-path> [-o OUTDIR] [--top N] [--since DATE]
                      [--exclude PATTERN] [--notes NOTES.md]

Outputs (in OUTDIR, default ./archaeology-report):
    archaeology-report.html   self-contained interactive report
    data.json                 raw analysis data
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "\x00ARCHAEOLOGY\x00"

DEFAULT_EXCLUDES = [
    "node_modules/", "vendor/", "dist/", "build/", "out/", "target/", "coverage/",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "composer.lock", "Gemfile.lock", "*.min.js", "*.min.css", "*.map", "*.lock",
]

LANG_BY_EXT = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".rs": "Rust", ".swift": "Swift", ".scala": "Scala", ".m": "ObjC",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".sql": "SQL", ".lua": "Lua", ".r": "R", ".pl": "Perl", ".dart": "Dart",
    ".vue": "Vue", ".svelte": "Svelte",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".less": "Less",
    ".md": "Markdown", ".rst": "reST", ".txt": "Text",
    ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".ini": "INI", ".json": "JSON",
    ".xml": "XML", ".gradle": "Gradle", ".proto": "Protobuf", ".graphql": "GraphQL",
}

# Extensions that participate in risk scoring / dependency graphs
CODE_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".go", ".cs", ".rb", ".php", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh",
    ".rs", ".swift", ".scala", ".vue", ".svelte", ".m", ".lua", ".dart",
}

# comment specs per language: (line prefixes, block pairs)
COMMENT_SPEC = {
    "Python": (["#"], [('"""', '"""'), ("'''", "'''")]),
    "JavaScript": (["//"], [("/*", "*/")]),
    "TypeScript": (["//"], [("/*", "*/")]),
    "Java": (["//"], [("/*", "*/")]),
    "Kotlin": (["//"], [("/*", "*/")]),
    "Go": (["//"], [("/*", "*/")]),
    "C#": (["//"], [("/*", "*/")]),
    "Ruby": (["#"], [("=begin", "=end")]),
    "PHP": (["//", "#"], [("/*", "*/")]),
    "C": (["//"], [("/*", "*/")]),
    "C++": (["//"], [("/*", "*/")]),
    "Rust": (["//"], [("/*", "*/")]),
    "Swift": (["//"], [("/*", "*/")]),
    "Scala": (["//"], [("/*", "*/")]),
    "Vue": (["//"], [("/*", "*/"), ("<!--", "-->")]),
    "Svelte": (["//"], [("/*", "*/"), ("<!--", "-->")]),
    "Shell": (["#"], []),
    "SQL": (["--"], [("/*", "*/")]),
    "Lua": (["--"], [("--[[", "]]")]),
    "HTML": ([], [("<!--", "-->")]),
    "CSS": ([], [("/*", "*/")]),
    "SCSS": (["//"], [("/*", "*/")]),
    "ObjC": (["//"], [("/*", "*/")]),
    "Dart": (["//"], [("/*", "*/")]),
}

JS_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"]

ENTRY_BASENAMES = {
    "main.py", "app.py", "run.py", "manage.py", "wsgi.py", "asgi.py", "__init__.py",
    "index.js", "index.ts", "main.js", "main.ts", "app.js", "app.ts", "server.js",
    "main.go", "main.java", "program.cs", "application.java", "main.rb", "mod.rs",
}

STOPWORDS = set("""
the a an this that these those with from into onto and or not no nor but if then than
when while for to of in on at by as is are was were be been being it its it's we you they
he she his her our your their all any both each few more most other some such only own same
so too very can will just should now don should've now aren doesn wasn weren hasn haven
ain aren couldn didn doesn hadn hasn haven isn ma mightn mustn needn shan shan't shouldn
wasn weren won wouldn
note notes see also todo fixme xxx param return returns param raises throw throws
use used uses using new get set got make made add added remove removed update updated
change changed check checked test tests case cases spec
value values data type types class file files line lines code method methods function
functions variable variables module modules package import exports default public private
static const async await await null true false none undefined string int bool float list
dict map array object number boolean error errors warning warnings version default params
args kwargs self this super return
first second third following above below here there which what who whose where why how
because due via per eg ie etc vs like unlike instead anyway however therefore thus hence
maybe probably currently old legacy original previous next last before after during
implement implements implementation support supported supports handle handles handler
create created delete deleted read write written
""".split())

MARKER_RE = re.compile(
    r"^\s*(TODO|FIXME|HACK|XXX|WORKAROUND|NOTE|WARNING|DEPRECATED|BUGBUG)\b[:\s]*(.*)",
    re.IGNORECASE,
)
DONT_TOUCH_RE = re.compile(
    r"\b(DO NOT|DON'T TOUCH|NEVER TOUCH|DOESN'T WORK|BROKEN|THIS IS A MESS|GOOD LUCK)\b(.*)",
    re.IGNORECASE,
)
BUGFIX_RE = re.compile(r"\b(fix|bug|hotfix|patch|revert|crash|regression|workaround)\b", re.IGNORECASE)
CAPS_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,}(?:[\s\-]+[A-Z][a-zA-Z0-9]{2,})+)\b")
SINGLE_CAP_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{3,})\b")
CAMEL_RE = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
SCREAMING_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,}[A-Z0-9_]*_[A-Z0-9_]+)\b")

MAX_FILE_BYTES = 1_000_000


# ----------------------------------------------------------------------------
# small utils
# ----------------------------------------------------------------------------

def log1p_norm(x, mx):
    if mx <= 0:
        return 0.0
    return math.log1p(max(0, x)) / math.log1p(mx)


def median(vals):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return 0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def is_excluded(path, patterns):
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    for pat in patterns:
        if pat.endswith("/"):
            if ("/" + p).find("/" + pat) >= 0:
                return True
        elif fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(p, pat):
            return True
    return False


def strip_ext(path):
    i = path.rfind(".")
    return path[:i] if i > path.rfind("/") else path


def read_text(root, rel):
    full = os.path.join(root, rel.replace("/", os.sep))
    try:
        size = os.path.getsize(full)
        if size > MAX_FILE_BYTES:
            return ""
        with open(full, "rb") as f:
            raw = f.read()
        if b"\x00" in raw[:8000]:
            return ""
        return raw.decode("utf-8", "replace")
    except OSError:
        return ""


# ----------------------------------------------------------------------------
# git access
# ----------------------------------------------------------------------------

def run_git(repo, args):
    r = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotepath=false"] + args,
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "git failed")
    return r.stdout.decode("utf-8", "replace")


def clean_rename(p):
    p = p.strip().strip('"')
    m = re.match(r"^(.*)\{(.*) => (.*)\}(.*)$", p)
    if m:
        pre, _old, new, suf = m.groups()
        return (pre + new + suf).replace("\\", "/")
    if " => " in p:
        p = p.split(" => ")[1]
    return p.replace("\\", "/").strip()


def parse_log(repo, since=None):
    # %x00 expands to a NUL byte in git's *output* (safe on Windows, where a literal
    # NUL inside an argv element would make CreateProcess fail).
    fmt = "%x00ARCHAEOLOGY%x00%H%x09%an%x09%ad%x09%s"
    args = ["log", "--no-merges", "--numstat", "--date=short", "--pretty=format:" + fmt]
    if since:
        args += ["--since", since]
    out = run_git(repo, args)
    commits = []
    cur = None
    for ln in out.splitlines():
        if not ln.strip():
            continue
        if ln.startswith(SEP):
            try:
                h, author, date, msg = ln[len(SEP):].split("\t", 3)
            except ValueError:
                continue
            cur = {"hash": h, "author": author.strip(), "date": date.strip(), "msg": msg, "files": []}
            commits.append(cur)
        elif cur is not None:
            parts = ln.split("\t")
            if len(parts) >= 3:
                a, d = parts[0], parts[1]
                p = clean_rename("\t".join(parts[2:]))
                if a == "-" and d == "-":
                    continue
                if p and not p.startswith(".."):
                    try:
                        cur["files"].append((p, int(a), int(d)))
                    except ValueError:
                        pass
    return commits


# ----------------------------------------------------------------------------
# comment extraction
# ----------------------------------------------------------------------------

def extract_comments(text, spec):
    line_prefixes, block_pairs = spec
    out = []
    lines = text.splitlines()
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        for p in line_prefixes:
            if s.startswith(p):
                t = s[len(p):].strip()
                if t and not t.startswith("!"):
                    out.append((i, t))
                break
    for start, end in block_pairs:
        idx = 0
        while True:
            a = text.find(start, idx)
            if a < 0:
                break
            b = text.find(end, a + len(start))
            if b < 0:
                b = min(len(text), a + 4000)
            line_no = text.count("\n", 0, a) + 1
            for part in text[a + len(start):b].split("\n"):
                part2 = part.strip().lstrip("*").strip()
                if part2 and not part2.startswith("!"):
                    out.append((line_no, part2))
                line_no += 1
            idx = b + len(end)
    return out


# ----------------------------------------------------------------------------
# dependency extraction
# ----------------------------------------------------------------------------

RE_PY_IMPORT = re.compile(r"^\s*import\s+([\w\.]+(?:\s*,\s*[\w\.]+)*)", re.M)
RE_PY_FROM = re.compile(r"^\s*from\s+([\.\w]+)\s+import", re.M)
RE_JS_FROM = re.compile(r"(?:import|export)\s[^'\"\n]*?from\s*['\"]([^'\"]+)['\"]", re.M)
RE_JS_SIDE = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"]", re.M)
RE_JS_CALL = re.compile(r"\b(?:require|import)\(\s*['\"]([^'\"]+)['\"]", re.M)
RE_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w\.]+)\s*;", re.M)
RE_GO_IMPORT_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
RE_GO_IMPORT_ONE = re.compile(r"^\s*import\s+(?:_\s+|[\w\.]+\s+)?\"([^\"]+)\"", re.M)
RE_CS_USING = re.compile(r"^\s*using\s+(?:static\s+)?([\w\.]+)\s*;", re.M)
RE_RB_REQUIRE = re.compile(r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]", re.M)
RE_PHP_USE = re.compile(r"^\s*use\s+([\w\\]+)", re.M)
RE_PHP_INCLUDE = re.compile(r"\b(?:include|require)(?:_once)?\s*\(?\s*['\"]([^'\"]+)['\"]", re.M)
RE_C_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def extract_imports(text, lang):
    """Return list of (spec, lang) import specs found in source text."""
    specs = []
    if lang == "Python":
        for m in RE_PY_IMPORT.finditer(text):
            for mod in m.group(1).split(","):
                specs.append((mod.strip().split(" as ")[0].strip(), "py"))
        for m in RE_PY_FROM.finditer(text):
            specs.append((m.group(1).strip(), "py"))
    elif lang in ("JavaScript", "TypeScript", "Vue", "Svelte"):
        for rx in (RE_JS_FROM, RE_JS_SIDE, RE_JS_CALL):
            for m in rx.finditer(text):
                specs.append((m.group(1), "js"))
    elif lang in ("Java", "Kotlin", "Scala"):
        for m in RE_JAVA_IMPORT.finditer(text):
            specs.append((m.group(1), "jvm"))
    elif lang == "Go":
        for m in RE_GO_IMPORT_BLOCK.finditer(text):
            for ln in m.group(1).splitlines():
                mm = re.search(r"\"([^\"]+)\"", ln)
                if mm:
                    specs.append((mm.group(1), "go"))
        for m in RE_GO_IMPORT_ONE.finditer(text):
            specs.append((m.group(1), "go"))
    elif lang == "C#":
        for m in RE_CS_USING.finditer(text):
            specs.append((m.group(1), "cs"))
    elif lang == "Ruby":
        for m in RE_RB_REQUIRE.finditer(text):
            specs.append((m.group(1), "rb"))
    elif lang == "PHP":
        for m in RE_PHP_USE.finditer(text):
            specs.append((m.group(1).replace("\\", "/"), "php"))
        for m in RE_PHP_INCLUDE.finditer(text):
            specs.append((m.group(1), "php-inc"))
    elif lang in ("C", "C++"):
        for m in RE_C_INCLUDE.finditer(text):
            specs.append((m.group(1), "c"))
    return specs


def resolve_relative_js(spec, importer_dir, fileset):
    base = spec
    root_alias = False
    for prefix in ("@/", "~/", "src/"):
        if base.startswith(prefix) and prefix != "src/":
            base = base[len(prefix):]
            root_alias = True
    if base.startswith("."):
        cur = importer_dir
        parts = base.split("/")
        for part in parts:
            if part == ".":
                continue
            elif part == "..":
                cur = cur.rsplit("/", 1)[0] if "/" in cur else ""
            else:
                cur = (cur + "/" + part) if cur else part
        cand_dir = cur
    elif root_alias:
        cand_dir = base
    else:
        return None  # bare module = external package
    cand_dir = cand_dir.lstrip("/")
    for ext in JS_EXTS:
        if cand_dir + ext in fileset:
            return cand_dir + ext
    for ext in JS_EXTS[1:]:
        if cand_dir + "/index" + ext in fileset:
            return cand_dir + "/index" + ext
    return None


def resolve_py(spec, importer, fileset):
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec.lstrip(".").replace(".", "/")
        cur_parts = importer.rsplit("/", 1)[0].split("/") if "/" in importer else []
        up = dots - 1
        if up:
            cur_parts = cur_parts[: len(cur_parts) - up] if len(cur_parts) >= up else []
        base = "/".join(cur_parts + ([rest] if rest else []))
    else:
        base = spec.replace(".", "/")
    for cand in (base + ".py", base + "/__init__.py"):
        if cand in fileset:
            return cand
    parts = base.split("/")
    for k in range(len(parts) - 1, 0, -1):
        pkg = "/".join(parts[:k]) + "/__init__.py"
        if pkg in fileset:
            return pkg
    return None


def build_dep_graph(files, lang_of, root, go_module):
    fileset = set(files)
    dotted_all = {}
    stem_map = defaultdict(set)
    for f in files:
        noext = strip_ext(f)
        dotted_all[noext.replace("/", ".")] = f
        stem_map[os.path.basename(noext)].add(f)

    edges = defaultdict(int)

    def add_edge(a, b):
        if a != b and a in fileset and b in fileset:
            edges[(a, b)] += 1

    for f in files:
        lang = lang_of.get(f)
        if not lang or lang not in CODE_LANGS:
            continue
        text = read_text(root, f)
        if not text:
            continue
        importer_dir = f.rsplit("/", 1)[0] if "/" in f else ""
        for spec, kind in extract_imports(text, lang):
            try:
                if kind == "py":
                    t = resolve_py(spec, f, fileset)
                elif kind == "js":
                    t = resolve_relative_js(spec, importer_dir, fileset)
                elif kind == "jvm":
                    t = dotted_all.get(spec)
                    if t is None:
                        t = dotted_all.get(spec.rsplit(".", 1)[0])
                    if t is None:
                        cls = spec.split(".")[-1]
                        hits = stem_map.get(cls, set())
                        if len(hits) == 1:
                            t = next(iter(hits))
                elif kind == "go":
                    t = None
                    if go_module and spec.startswith(go_module + "/"):
                        rel = spec[len(go_module) + 1:]
                        if rel + ".go" in fileset:
                            t = rel + ".go"
                        else:
                            in_dir = [x for x in files if x.startswith(rel + "/") and x.endswith(".go")]
                            if 0 < len(in_dir) <= 8:
                                t = in_dir[0]
                elif kind == "cs":
                    t = dotted_all.get(spec)
                    if t is None:
                        cls = spec.split(".")[-1]
                        hits = stem_map.get(cls, set())
                        if len(hits) == 1:
                            t = next(iter(hits))
                elif kind == "rb":
                    if f.endswith(".rb"):
                        t = None
                        cand = (importer_dir + "/" + spec if spec.startswith(".") else spec).lstrip("./")
                        if cand + ".rb" in fileset:
                            t = cand + ".rb"
                        elif spec + ".rb" in fileset:
                            t = spec + ".rb"
                        else:
                            hits = stem_map.get(os.path.basename(spec), set())
                            if len(hits) == 1:
                                t = next(iter(hits))
                    else:
                        t = None
                elif kind == "php":
                    cand = spec.strip("./")
                    hits = [x for x in files if x.endswith(cand + ".php")]
                    t = hits[0] if len(hits) == 1 else None
                elif kind == "php-inc":
                    cand = os.path.normpath(importer_dir + "/" + spec).replace("\\", "/").lstrip("./")
                    t = cand if cand in fileset else None
                elif kind == "c":
                    cand = (importer_dir + "/" + spec).lstrip("./")
                    if cand in fileset:
                        t = cand
                    else:
                        hits = stem_map.get(strip_ext(spec).rsplit("/", 1)[-1], set())
                        t = next(iter(hits)) if len(hits) == 1 else None
                else:
                    t = None
            except Exception:
                t = None
            if t:
                add_edge(f, t)

    fan_in = defaultdict(int)
    fan_out = defaultdict(int)
    for (a, b), w in edges.items():
        fan_out[a] += w
        fan_in[b] += w
    adj = defaultdict(set)
    radj = defaultdict(set)
    for (a, b) in edges:
        adj[a].add(b)
        radj[b].add(a)
    return edges, dict(fan_in), dict(fan_out), adj, radj


def find_cycles(adj, radj, nodes):
    """Iterative Kosaraju SCC; return cycles (component size > 1)."""
    if not nodes:
        return []
    node_set = set(nodes)
    empty = frozenset()
    order, visited = [], set()
    for start in nodes:
        if start in visited:
            continue
        stack = [(start, iter(sorted(adj.get(start, empty) & node_set)))]
        visited.add(start)
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append((nxt, iter(sorted(adj.get(nxt, empty) & node_set))))
                    advanced = True
                    break
            if not advanced:
                order.append(node)
                stack.pop()
    comp = {}
    cid = 0
    for start in reversed(order):
        if start in comp:
            continue
        stack = [start]
        comp[start] = cid
        while stack:
            node = stack.pop()
            for nxt in radj.get(node, empty) & node_set:
                if nxt not in comp:
                    comp[nxt] = cid
                    stack.append(nxt)
        cid += 1
    groups = defaultdict(list)
    for n, c in comp.items():
        groups[c].append(n)
    cycles = [sorted(g) for g in groups.values() if len(g) > 1]
    cycles.sort(key=len, reverse=True)
    return cycles[:15]


# ----------------------------------------------------------------------------
# external dependency manifests
# ----------------------------------------------------------------------------

def scan_external_deps(root, tracked):
    found = defaultdict(set)

    def read(rel):
        return read_text(root, rel)

    for f in tracked:
        base = f.rsplit("/", 1)[-1]
        if base == "package.json":
            try:
                j = json.loads(read(f))
                for sec in ("dependencies", "devDependencies", "peerDependencies"):
                    for name in (j.get(sec) or {}):
                        found["npm"].add(name)
            except Exception:
                pass
        elif re.match(r"^requirements[\w\-]*\.txt$", base):
            for ln in read(f).splitlines():
                ln = ln.strip()
                if ln and not ln.startswith(("-", "#")):
                    found["pip"].add(re.split(r"[<>=!~\[; ]", ln, maxsplit=1)[0].strip())
        elif base == "pyproject.toml":
            txt = read(f)
            m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", txt, re.S | re.M)
            if m:
                for mm in re.finditer(r"['\"]([A-Za-z0-9_.\-]+)[^'\"]*['\"]", m.group(1)):
                    found["pip"].add(mm.group(1))
        elif base == "go.mod":
            txt = read(f)
            mm = re.search(r"^module\s+(\S+)", txt, re.M)
            mod = mm.group(1) if mm else ""
            for m in re.finditer(r"^\s+([\w\.\-/]+\.[\w\.\-/]+)\s+v[\d\w]", txt, re.M):
                name = m.group(1)
                if name != mod:
                    found["go"].add(name.rsplit("/", 1)[-1])
        elif base == "pom.xml":
            txt = read(f)
            for m in re.finditer(r"<artifactId>([^<]+)</artifactId>", txt):
                found["maven"].add(m.group(1))
        elif base == "Gemfile":
            txt = read(f)
            for m in re.finditer(r"gem\s+['\"]([^'\"]+)['\"]", txt):
                found["rubygems"].add(m.group(1))
        elif base == "composer.json":
            try:
                j = json.loads(read(f))
                for sec in ("require", "require-dev"):
                    for name in (j.get(sec) or {}):
                        if name != "php":
                            found["packagist"].add(name)
            except Exception:
                pass
        elif base == "Cargo.toml":
            txt = read(f)
            sec = None
            for ln in txt.splitlines():
                s = ln.strip()
                if s.startswith("["):
                    sec = s.strip("[]")
                    continue
                if sec == "dependencies" and "=" in s and not s.startswith("#"):
                    found["crates"].add(s.split("=", 1)[0].strip().strip('"'))
        elif base.endswith(".csproj"):
            txt = read(f)
            for m in re.finditer(r'PackageReference\s+Include="([^"]+)"', txt):
                found["nuget"].add(m.group(1))

    return [
        {"eco": eco, "total": len(deps), "names": sorted(deps)}
        for eco, deps in sorted(found.items(), key=lambda kv: -len(kv[1]))
    ]


# ----------------------------------------------------------------------------
# glossary + wall
# ----------------------------------------------------------------------------

def mine_comments(root, files, lang_of):
    term_count = Counter()
    term_files = defaultdict(set)
    term_sample = {}
    wall = []
    id_set = set()

    for f in files:
        lang = lang_of.get(f)
        spec = COMMENT_SPEC.get(lang)
        text = read_text(root, f)
        if not text:
            continue
        for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
            id_set.add(tok)
        if not spec:
            continue
        for line_no, c in extract_comments(text, spec):
            if len(c) < 3:
                continue
            for m in CAPS_PHRASE_RE.finditer(c):
                register(term_count, term_files, term_sample, m.group(1), c, f)
            for m in SINGLE_CAP_RE.finditer(c):
                register(term_count, term_files, term_sample, m.group(1), c, f)
            for m in CAMEL_RE.finditer(c):
                register(term_count, term_files, term_sample, m.group(1), c, f)
            for m in SCREAMING_RE.finditer(c):
                t = m.group(1)
                if not t.startswith("TODO"):
                    register(term_count, term_files, term_sample, t, c, f)

            mm = MARKER_RE.match(c)
            if mm:
                marker, body = mm.group(1).upper(), mm.group(2).strip()
                if body and len(body) >= 4:
                    wall.append({"marker": marker, "text": body[:160], "path": f, "line": line_no})
            else:
                dm = DONT_TOUCH_RE.search(c)
                if dm:
                    body = (dm.group(1) + dm.group(2)).strip()
                    wall.append({"marker": "WARNING", "text": body[:160], "path": f, "line": line_no})

    glossary = []
    for term, cnt in term_count.most_common(600):
        if cnt < 3 or len(term) < 4 or term.lower() in STOPWORDS:
            continue
        if term.upper() in ("TODO", "FIXME", "HACK", "XXX", "NOTE", "WARNING",
                            "WORKAROUND", "DEPRECATED", "BUGBUG", "NB"):
            continue
        words = re.split(r"[\s\-]+", term)
        if any(w.lower() in STOPWORDS for w in words):
            continue
        snake = term.lower().replace(" ", "_").replace("-", "_")
        in_code = snake in id_set or all(w in id_set for w in words)
        # bare sentence words ("Everything", "Dave") only qualify when they are real
        # identifiers used somewhere in the code
        if len(words) == 1 and not in_code:
            continue
        glossary.append({
            "term": term,
            "count": cnt,
            "files": len(term_files[term]),
            "sample": term_sample.get(term, "")[:160],
            "in_code": in_code,
        })
    glossary.sort(key=lambda g: (not g["in_code"], -g["count"]))
    return glossary[:60], wall


def register(counter, files_map, sample_map, term, comment, path):
    counter[term] += 1
    files_map[term].add(path)
    if term not in sample_map:
        sample_map[term] = comment


# ----------------------------------------------------------------------------
# main analysis
# ----------------------------------------------------------------------------

CODE_LANGS = {lang for lang in set(LANG_BY_EXT.values()) if lang in COMMENT_SPEC or lang in
              ("JavaScript", "TypeScript", "Vue", "Svelte", "Python", "Java", "Kotlin", "Go",
               "C#", "Ruby", "PHP", "C", "C++", "Rust", "Swift", "Scala", "ObjC", "Lua", "Dart")}


def analyze(repo, outdir, top_n=20, since=None, excludes=None, notes_path=None):
    excludes = (excludes or []) + DEFAULT_EXCLUDES
    repo = os.path.abspath(repo)

    try:
        run_git(repo, ["rev-parse", "HEAD"])
    except Exception as e:
        print("Not a usable git repository: %s" % e, file=sys.stderr)
        sys.exit(2)

    commits = parse_log(repo, since)
    if not commits:
        print("No commits found (nothing to excavate).", file=sys.stderr)
        sys.exit(2)

    tracked = [t.strip() for t in run_git(repo, ["ls-files"]).splitlines() if t.strip()]
    tracked = [t for t in tracked if not is_excluded(t, excludes)]
    try:
        branch = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except Exception:
        branch = "HEAD"

    # ---- file stats from git history ----
    stats = defaultdict(lambda: {"commits": 0, "add": 0, "del": 0, "authors": set(),
                                 "first": None, "last": None, "bugfixes": 0})
    timeline = defaultdict(lambda: {"commits": 0, "add": 0, "del": 0})
    authors = Counter()
    author_sets = set()

    for c in commits:
        month = c["date"][:7]
        timeline[month]["commits"] += 1
        authors[c["author"]] += 1
        is_bug = bool(BUGFIX_RE.search(c["msg"]))
        for p, a, d in c["files"]:
            if is_excluded(p, excludes):
                continue
            s = stats[p]
            s["commits"] += 1
            s["add"] += a
            s["del"] += d
            s["authors"].add(c["author"])
            if s["first"] is None or c["date"] < s["first"]:
                s["first"] = c["date"]
            if s["last"] is None or c["date"] > s["last"]:
                s["last"] = c["date"]
            if is_bug:
                s["bugfixes"] += 1
            timeline[month]["add"] += a
            timeline[month]["del"] += d

    # ---- current files, loc, languages ----
    lang_of = {}
    loc_of = {}
    lang_stats = defaultdict(lambda: {"files": 0, "lines": 0})
    for f in tracked:
        ext = f.rsplit(".", 1)[-1].lower()
        ext = "." + ext if "." + ext != f else ""
        lang = LANG_BY_EXT.get(ext)
        if not lang:
            continue
        lang_of[f] = lang
        text = read_text(repo, f)
        n = text.count("\n") + (1 if text else 0)
        loc_of[f] = n
        lang_stats[lang]["files"] += 1
        lang_stats[lang]["lines"] += n

    # ---- dependencies ----
    go_module = ""
    gm = os.path.join(repo, "go.mod")
    if os.path.exists(gm):
        try:
            m = re.search(r"^module\s+(\S+)", open(gm, encoding="utf-8", errors="replace").read(), re.M)
            go_module = m.group(1) if m else ""
        except OSError:
            pass

    code_files = [f for f in tracked if lang_of.get(f) in CODE_LANGS]
    edges, fan_in, fan_out, adj, radj = build_dep_graph(code_files, lang_of, repo, go_module)
    graph_all_nodes = sorted({n for e in edges for n in e})
    cycles = find_cycles(adj, radj, graph_all_nodes) if edges else []

    external = scan_external_deps(repo, tracked)

    # ---- comments mining ----
    glossary, wall = mine_comments(repo, code_files, lang_of)

    # ---- risk scoring ----
    test_stems = set()
    for f in tracked:
        b = os.path.basename(strip_ext(f))
        for pre in ("test_",):
            if b.startswith(pre):
                test_stems.add(b[len(pre):])
        for suf in ("_test", ".test", ".spec", "_spec", "Test", "Tests"):
            if b.endswith(suf) and len(b) > len(suf):
                test_stems.add(b[: -len(suf)])
    entry_paths = {f for f in tracked if os.path.basename(f) in ENTRY_BASENAMES}

    existing = [f for f in tracked if f in stats or f in loc_of]
    cur = []
    for f in sorted(set(existing)):
        s = stats.get(f, {"commits": 0, "add": 0, "del": 0, "authors": set(), "first": None,
                          "last": None, "bugfixes": 0})
        if lang_of.get(f) not in CODE_LANGS:
            continue
        loc = loc_of.get(f, 0)
        c = s["commits"]
        fi = fan_in.get(f, 0)
        fo = fan_out.get(f, 0)
        stem = os.path.basename(strip_ext(f))
        tested = stem in test_stems
        cur.append({"path": f, "lang": lang_of.get(f), "loc": loc, "commits": c,
                    "add": s["add"], "del": s["del"], "churn": s["add"] + s["del"],
                    "bugfixes": s["bugfixes"], "authors_n": len(s["authors"]),
                    "fan_in": fi, "fan_out": fo, "tested": tested,
                    "last": s["last"] or "", "first": s["first"] or ""})

    max_c = max((x["commits"] for x in cur), default=0)
    max_loc = max((x["loc"] for x in cur), default=0)
    max_fi = max((x["fan_in"] for x in cur), default=0)
    med_c = median([x["commits"] for x in cur])
    med_loc = median([x["loc"] for x in cur])

    for x in cur:
        n_c = log1p_norm(x["commits"], max_c)
        n_loc = log1p_norm(x["loc"], max_loc)
        n_fi = log1p_norm(x["fan_in"], max_fi)
        bug_ratio = (x["bugfixes"] / x["commits"]) if x["commits"] else 0.0
        x["risk"] = int(round(100 * (0.28 * n_c + 0.22 * bug_ratio + 0.18 * n_loc
                                     + 0.22 * n_fi + (0.10 if not x["tested"] else 0.0))))
        hot = x["commits"] >= med_c
        big = x["loc"] >= med_loc
        if hot and big:
            x["quad"] = "refactor"
        elif hot:
            x["quad"] = "workhorse"
        elif big:
            x["quad"] = "landmine"
        else:
            x["quad"] = "backwater"

    cur.sort(key=lambda x: (-x["risk"], -x["commits"]))

    zombies = [
        {"path": x["path"], "loc": x["loc"], "commits": x["commits"], "fan_in": x["fan_in"],
         "last": x["last"]}
        for x in cur
        if x["commits"] <= 1 and x["fan_in"] == 0 and x["loc"] >= 40
        and os.path.basename(x["path"]) not in ENTRY_BASENAMES and not x["path"].startswith("test")
    ][:20]

    quads = defaultdict(list)
    for x in cur:
        quads[x["quad"]].append(x["path"])
    quad_summary = {k: {"count": len(v), "top": sorted(v)[:6]} for k, v in quads.items()}

    tested_code = [x for x in cur if x["loc"] >= 20]
    tested_ratio = (sum(1 for x in tested_code if x["tested"]) / len(tested_code)) if tested_code else None

    # ---- ghosts (deleted files with heavy history) ----
    existing_set = set(tracked)
    ghosts = [
        {"path": p, "commits": s["commits"], "churn": s["add"] + s["del"], "last": s["last"]}
        for p, s in stats.items() if p not in existing_set
    ]
    ghosts.sort(key=lambda g: -g["commits"])
    ghosts = ghosts[:15]

    # ---- dependency graph payload ----
    graph_nodes_files = sorted(
        {n for e in edges for n in e},
        key=lambda f: -(2 * fan_in.get(f, 0) + fan_out.get(f, 0) + math.log1p(loc_of.get(f, 0))),
    )[:80]
    keep = set(graph_nodes_files)
    links = [{"s": a, "t": b, "w": w} for (a, b), w in edges.items() if a in keep and b in keep]
    nodes = [{
        "id": f,
        "group": f.split("/", 1)[0] if "/" in f else ".",
        "loc": loc_of.get(f, 0),
        "fan_in": fan_in.get(f, 0),
        "fan_out": fan_out.get(f, 0),
    } for f in graph_nodes_files]
    nodes.sort(key=lambda n: -n["fan_in"])

    orphans = [
        {"path": x["path"], "loc": x["loc"], "fan_out": x["fan_out"], "commits": x["commits"]}
        for x in cur
        if x["fan_in"] == 0 and x["loc"] >= 80 and os.path.basename(x["path"]) not in ENTRY_BASENAMES
        and not x["path"].startswith(("test", "tests/"))
    ][:15]

    # ---- assemble ----
    first_date = min(c["date"] for c in commits)
    last_date = max(c["date"] for c in commits)
    d1 = datetime.strptime(first_date, "%Y-%m-%d")
    d2 = datetime.strptime(last_date, "%Y-%m-%d")
    years = (d2 - d1).days / 365.25

    langs = sorted(
        ({"name": k, "files": v["files"], "lines": v["lines"]} for k, v in lang_stats.items()),
        key=lambda l: -l["lines"],
    )

    notes = ""
    if notes_path:
        try:
            with open(notes_path, encoding="utf-8") as fh:
                notes = fh.read()
        except OSError as e:
            print("Could not read notes file: %s" % e, file=sys.stderr)

    data = {
        "meta": {
            "repo": os.path.basename(repo.rstrip("/\\")),
            "branch": branch,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "first": first_date, "last": last_date,
            "years": round(years, 1),
            "commits": len(commits),
            "contributors": len(authors),
            "files": len(tracked),
            "loc": sum(lang_stats[l]["lines"] for l in CODE_LANGS if l in lang_stats),
            "languages": langs,
            "tested_ratio": round(tested_ratio, 2) if tested_ratio is not None else None,
            "cycle_count": len(cycles),
            "internal_edges": len(edges),
        },
        "timeline": [
            {"m": m, "commits": v["commits"], "add": v["add"], "del": v["del"]}
            for m, v in sorted(timeline.items())
        ][-48:],
        "authors": [{"name": n, "commits": c} for n, c in authors.most_common(15)],
        "hotspots": cur[:400],
        "ghosts": ghosts,
        "risk": {"med_commits": med_c, "med_loc": med_loc, "quads": quad_summary,
                 "zombies": zombies, "orphans": orphans},
        "deps": {"nodes": nodes, "links": links, "cycles": cycles[:12], "external": external},
        "glossary": glossary,
        "wall": wall[:100],
        "notes": notes,
    }

    # ---- write outputs ----
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets",
                                 "report_template.html")
    with open(template_path, encoding="utf-8") as fh:
        template = fh.read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("/*__DATA__*/null", payload).replace(
        "/*__NOTES__*/null", json.dumps(notes))
    report_path = os.path.join(outdir, "archaeology-report.html")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    # ---- console summary ----
    print("Excavation complete -> %s" % report_path)
    print("-" * 64)
    m = data["meta"]
    print("repo %s @ %s | %s .. %s (%.1f yrs) | %d commits, %d contributors" % (
        m["repo"], m["branch"], m["first"], m["last"], m["years"], m["commits"], m["contributors"]))
    print("files %d | code loc %d | internal dep edges %d | cycles %d | tested %.0f%%" % (
        m["files"], m["loc"], m["internal_edges"], m["cycle_count"],
        (m["tested_ratio"] or 0) * 100))
    print("\nTop refactoring risks:")
    for x in cur[:top_n]:
        print("  %3d risk  %-42s %4d commits  fan-in %d" % (
            x["risk"], x["path"][:42], x["commits"], x["fan_in"]))
    if zombies:
        print("\nZombie files (cold, nothing imports them): %d" % len(zombies))
    if cycles:
        print("Circular dependency chains: %d (see report)" % len(cycles))
    return data


def main():
    ap = argparse.ArgumentParser(description="Repo Archaeologist — legacy codebase excavator")
    ap.add_argument("repo", help="path to the git repository to analyze")
    ap.add_argument("-o", "--outdir", default="archaeology-report", help="output directory")
    ap.add_argument("--top", type=int, default=20, help="how many risk entries to print")
    ap.add_argument("--since", default=None, help="only analyze commits after this date (YYYY-MM-DD)")
    ap.add_argument("--exclude", action="append", default=[], help="extra exclude glob (repeatable)")
    ap.add_argument("--notes", default=None, help="markdown file with agent notes to embed in report")
    args = ap.parse_args()
    analyze(args.repo, args.outdir, args.top, args.since, args.exclude, args.notes)


if __name__ == "__main__":
    main()
