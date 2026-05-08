# remarkable2zotero

Extract highlight annotations from a reMarkable tablet and create structured notes in a Zotero group library.

## How it works

1. Connects to your reMarkable via SSH
2. Discovers PDFs and epubs with highlight annotations
3. Parses `.rm` v6 annotation files to extract highlighted text
4. Finds the matching item in your Zotero group library
5. Creates a "reMarkable Highlights" note as a child of the item, with all highlighted text grouped by page

The original PDF/epub in Zotero is left untouched. Re-running sync replaces the existing highlights note with an updated version.

## Why notes instead of PDF highlight annotations

Embedding highlights directly into the PDF was considered but rejected. The reMarkable strips whitespace at line breaks when storing highlighted text (e.g. "yethistoriography" instead of "yet historiography"), and PDF text-position matching returns partial matches on long strings, producing highlights that only cover the first few words of a passage. Epub-to-PDF conversion compounds the problem since page indices don't correspond between the reMarkable and the converted PDF.

The highlighted text extracted from the reMarkable's `GlyphRange` objects is already clean and complete. Creating a Zotero note captures every highlight reliably, grouped by page, without depending on fragile coordinate transforms or text-position matching. The note is searchable across the library, and the original document attachment is preserved exactly as it was.

The `extract` command is available separately for generating locally annotated PDFs if needed.

## Requirements

- Python 3.10+
- reMarkable tablet with SSH enabled (firmware 3.6+)
- Zotero account with API key and group library access

## Setup

```bash
git clone <repo-url> && cd remarkable
make setup
```

Copy the example config and fill in your credentials:

```bash
mkdir -p ~/.config/remarkable2zotero
cp config.example.yaml ~/.config/remarkable2zotero/config.yaml
```

Edit `~/.config/remarkable2zotero/config.yaml`:

```yaml
remarkable:
  host: "10.11.99.1"          # USB default; use WiFi IP if wireless
  user: "root"
  password: ""                 # Settings > General > Help > Copyrights and licenses

zotero:
  api_key: ""                  # https://www.zotero.org/settings/keys
  group_id: 0                  # Numeric ID from group URL
```

## Usage

```bash
make sync                      # interactive: choose which documents to sync
make sync ARGS="--dry-run"     # preview without uploading
make sync ARGS="-d 'Paper'"   # process a single document by name
make sync ARGS="--no-interact" # process all without prompting
make list                      # list annotated documents on the device
make extract                   # extract locally annotated PDFs (no Zotero)
```

Running `make sync` presents a numbered list of annotated documents and prompts you to select which to process (e.g. `1,3` or `2-4` or `a` for all).

## How matching works

The tool matches reMarkable documents to existing Zotero items by parsing the document name (typically `Author - Year - Title`) and searching the group library. It extracts the title portion, strips year and author prefixes, and compares against both parent item titles and attachment filenames with punctuation-insensitive matching. All collection memberships, tags, and metadata on the matched item are preserved.

Use `--force-create` to create new Zotero items for documents that don't have an existing match.

## How annotation extraction works

The reMarkable stores highlights as `GlyphRange` objects in its `.rm` v6 annotation files. Each `GlyphRange` contains the highlighted text string, rectangle coordinates, and color. The tool extracts these using the `rmscene` library.

The `GlyphRange` text is the authoritative source — it comes directly from the reMarkable's text layer and does not require OCR or coordinate-to-text mapping. Both PDFs and epubs use the same annotation format.

## Configuration

Config values can also be set via environment variables:

| Variable | Config key |
|---|---|
| `R2Z_REMARKABLE_HOST` | `remarkable.host` |
| `R2Z_REMARKABLE_PASSWORD` | `remarkable.password` |
| `R2Z_ZOTERO_API_KEY` | `zotero.api_key` |
| `R2Z_ZOTERO_GROUP_ID` | `zotero.group_id` |

## Known issues

- **rmscene warnings.** "Some data has not been read" warnings from the rmscene library are harmless. They indicate that firmware 3.25.x writes some newer fields that rmscene 0.8.0 doesn't recognise, but highlight data is parsed correctly.
- **Line-break concatenation.** The reMarkable sometimes strips whitespace at line breaks when storing highlighted text (e.g. "yethistoriography" instead of "yet historiography"). This affects the note text but is generally readable in context. It does not cause data loss.

## Development

```bash
make test                      # run tests
make lint                      # run ruff linter
make clean                     # remove .venv and caches
```
