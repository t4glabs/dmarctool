"""
Registered-domain detection from the Public Suffix List, bundled in
app/public_suffix_list.dat (ICANN section only) so nothing is fetched at run
time.

This replaced an 8-entry list of two-part endings, which mishandled every
ending it didn't name: for a .edu.in domain it checked the expiry of `edu.in`
itself, and look-alikes of `x.org.in` came out as `x.0rg.in`. The PSL is the
same data browsers and every DMARC receiver use to find a domain's
organizational domain, so Vigil now agrees with them.

Only the ICANN section: private entries (blogspot.com, github.io...) are
hosting customers' subdomains, not something anyone renews at a registrar.

To refresh the bundled copy:
    curl -sS https://publicsuffix.org/list/public_suffix_list.dat -o /tmp/psl.dat
then keep the lines between "===BEGIN ICANN DOMAINS===" and
"===END ICANN DOMAINS===" without comments or blank lines, under the 3-line
header at the top of app/public_suffix_list.dat.
"""

from pathlib import Path

_PSL_PATH = Path(__file__).resolve().parent / "public_suffix_list.dat"
_rules = None


def _load():
    global _rules
    if _rules is not None:
        return _rules
    normal, wildcard, exception = set(), set(), set()
    for line in _PSL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            line = ".".join(label.encode("idna").decode("ascii") if label != "*" else label
                            for label in line.lstrip("!").split(".")) if not line.isascii() else line
        except UnicodeError:
            continue
        if line.startswith("!"):
            exception.add(line[1:])
        elif line.startswith("*."):
            wildcard.add(line[2:])
        else:
            normal.add(line)
    _rules = (normal, wildcard, exception)
    return _rules


def public_suffix(name: str) -> str:
    """The public suffix of `name` (e.g. "edu.in" for "x.edu.in"), by the
    PSL algorithm: an exception rule wins, else the longest matching rule,
    else the last label alone (the PSL's implicit "*" rule)."""
    normal, wildcard, exception = _load()
    labels = name.lower().rstrip(".").split(".")
    best = 1
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        size = len(labels) - i
        if candidate in exception:
            return ".".join(labels[i + 1:])
        if candidate in normal:
            best = max(best, size)
        if i + 1 < len(labels) and ".".join(labels[i + 1:]) in wildcard:
            best = max(best, size)
    return ".".join(labels[-best:])


def registrable_domain(name: str) -> str:
    """The domain someone actually registered: the public suffix plus one
    more label (mail.aikyamhq.com -> aikyamhq.com, x.edu.in -> x.edu.in,
    prerna.aikyam.school -> aikyam.school). A name that IS a public suffix
    is returned unchanged -- there is nothing narrower to give back."""
    name = name.lower().rstrip(".")
    suffix = public_suffix(name)
    if name == suffix:
        return name
    head = name[: -len(suffix) - 1]
    return f"{head.rsplit('.', 1)[-1]}.{suffix}"
