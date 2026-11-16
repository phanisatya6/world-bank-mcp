# Presentation — World Bank Documents MCP Server

This folder holds the interview presentation that accompanies the project.

| File | What it is |
| --- | --- |
| `World-Bank-MCP-Presentation.pptx` | The slide deck (26 slides, 16:9) |
| `output_screenshots/` | Terminal images used on the "Output" slides |
| `build_deck.py` | Script that regenerates the deck and the images |
| `README.md` | This file |

Submit the `.pptx` (and optionally this whole folder) **alongside** the project zip.

---

## Slide map

| # | Slide | Notes |
| --- | --- | --- |
| 1 | Title | Fill in your **name** and **date** |
| 2 | Agenda | |
| 3 | 1. The Task | What was required + what was out of scope |
| 4 | 2. What Was Delivered | File-by-file table |
| 5 | 3. Architecture | Client → MCP server → HTTP layer → WBA API |
| 6 | 4. The Four MCP Tools | Tool / purpose / API params |
| 7 | 5. Tool Design for an LLM | Docstring sample |
| 8 | 6. Pydantic Validation & Error Handling | |
| 9 | 7. The Client Agent Loop | The nine required steps |
| 10 | 7b. MCP → LLM Schema Conversion | Runtime conversion code |
| 11 | 8. LLM Choice & Configuration | Groq default, Gemini/Ollama alternatives |
| 12 | 9. Live API Quirks & Fallbacks | WDS API edge cases |
| 13 | 10. Free-Tier Rate Limits & Robustness | 8,000 TPM strategy |
| **14** | **11. Output — Agent Answer** | Screenshot slot (pre-filled with a live run) |
| **15** | **11b. Output — Tool-Call Trace** | Screenshot slot |
| **16** | **11c. Output — Test Suite** | Screenshot slot |
| **17** | **11d. Output — Facet Discovery** | Screenshot slot |
| **18** | **11e. Output — Your Own Screenshots** | **Two empty dashed slots — paste your screenshots here** |
| 19 | 12.1 Example 1 — Climate Resilience in Kenya | |
| 20 | 12.2 Example 2 — Education Financing, Sub-Saharan Africa | |
| 21 | 12.3 Example 3 — Most Frequent Document Types | |
| 22 | 13. Testing | 66 passed, 2 skipped |
| 23 | 14. Project Structure | |
| 24 | 15. How to Run | |
| 25 | 16. Design Notes & Limitations | |
| 26 | 17. Summary | |

Slides **14–17** already contain real captured output. If you would rather show
your *own* screenshots, delete the picture on the slide and paste yours into the
same area (or use the blank slots on slide **18**).

---

## The "output screen slots"

Slides 14–18 are the output slots. Slide 18 has two genuinely empty dashed
placeholders. To fill a slot in PowerPoint:

1. Open the slide.
2. Insert → Pictures, or just paste a screenshot with `Ctrl+V`.
3. Drag it over the dashed box and resize to fit.

### Commands to capture (matching the slides)

| Slide | Command to run and screenshot |
| --- | --- |
| 14 — Agent Answer | `python -m client.agent "What World Bank reports exist on climate resilience in Kenya?"` |
| 15 — Tool-Call Trace | `python -m client.agent --show-tools "What document types does the World Bank publish most frequently?"` |
| 16 — Test Suite | `python -m pytest -q` |
| 17 — Facet Discovery | `python -m client.agent "What document types does the World Bank publish most frequently?"` |
| 18 — Your own | `python scripts/smoke_test.py` · `python -m client.agent --interactive` · MCP Inspector |

> Tip: make the terminal window narrow-ish and increase the font size before
> screenshotting so the text is readable when projected.

---

## Regenerating the deck

```powershell
# from this folder, with the project venv active
pip install python-pptx pillow
python build_deck.py
```

`build_deck.py` reads the captured command output from
`%LOCALAPPDATA%\Temp\opencode\wb-captures\*.txt`, re-renders the terminal
images into `output_screenshots/`, and rewrites the `.pptx`. If a capture file is
missing, the corresponding slide falls back to an empty dashed slot.

---

## What was captured for the deck

The images in `output_screenshots/` come from real runs on this machine:

- `01_agent_answer.png` — a full Groq + live-API agent run
- `02_tool_trace.png` — `--show-tools` stderr trace
- `03_pytest.png` — the offline test suite result
- `04_document_types.png` — a `get_facets` answer
- `05_list_tools.png` — the four schemas discovered at runtime
- `06_check_llm.png` — provider/key verification
