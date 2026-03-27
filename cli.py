"""
cli.py
──────
PipelineIQ — Internal GPT for CI/CD failure intelligence.
Interactive terminal chat. No frontend. No API keys.

Usage:
  python cli.py                        # Interactive mode
  python cli.py --log build.log        # Analyze a log file directly
  python cli.py --pipe github-actions  # Set pipeline type
  python cli.py --model mistral        # Use a different Ollama model
  python cli.py --teach                # Record a resolved failure
  python cli.py --stats                # Show knowledge base stats
"""

import sys
import json
import argparse
import textwrap
from pathlib import Path
from analyzer import PipelineAnalyzer

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    PURPLE = "\033[95m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    BG_RED = "\033[41m"

def col(color, text): return f"{color}{text}{C.RESET}"
def bold(text):       return col(C.BOLD, text)
def dim(text):        return col(C.DIM, text)




HELP_TEXT = f"""
{bold('Commands:')}
  {col(C.GREEN, 'analyze')}   — Analyze a failure log (paste or load file)
  {col(C.GREEN, 'teach')}     — Record a resolved failure (improves future answers)
  {col(C.GREEN, 'stats')}     — Show knowledge base stats
  {col(C.GREEN, 'models')}    — List available Ollama models
  {col(C.GREEN, 'switch')}    — Switch LLM model
  {col(C.GREEN, 'clear')}     — Clear conversation history
  {col(C.GREEN, 'help')}      — Show this help
  {col(C.GREEN, 'exit')}      — Quit

{bold('During analysis, you can ask follow-up questions like:')}
  {dim('> How do I prevent this in future pipelines?')}
  {dim('> Show me the exact Dockerfile change needed')}
  {dim('> What other services could be affected?')}
  {dim('> Explain what a state lock is')}
"""

# ── Display helpers ───────────────────────────────────────────────────────────

SEV_COLOR = {"critical": C.BG_RED, "high": C.RED, "medium": C.YELLOW, "low": C.GREEN, "info": C.CYAN}
CONF_COLOR = {"High": C.GREEN, "Medium": C.YELLOW, "Low": C.RED}

def hr(char="─", width=72, color=C.DIM):
    print(col(color, char * width))

def section(title, color=C.PURPLE):
    print(f"\n{col(color, C.BOLD + f'  ◆  {title}  ' + C.RESET)}")
    hr()

def print_analysis(result: dict):
    """Pretty-print the structured analysis result."""

    section("ROOT CAUSE ANALYSIS", C.RED)

    rc = result.get("root_cause", "Unknown")
    print(f"\n  {col(C.RED, bold('⚠  Root Cause:'))}  {rc}\n")

    stage     = result.get("stage", "unknown")
    etype     = result.get("error_type", "Unknown")
    conf      = result.get("confidence", "Low")
    conf_c    = CONF_COLOR.get(conf, C.WHITE)

    print(f"  {dim('Stage:')}       {col(C.CYAN, stage)}")
    print(f"  {dim('Error Type:')}  {col(C.YELLOW, etype)}")
    print(f"  {dim('Confidence:')}  {col(conf_c, conf)}")

    if explanation := result.get("explanation"):
        section("WHY IT HAPPENED", C.BLUE)
        wrapped = textwrap.fill(explanation, width=68, initial_indent="  ", subsequent_indent="  ")
        print(f"\n{wrapped}\n")

    if fix := result.get("fix"):
        section("THE FIX", C.GREEN)
        print(f"\n  {bold(fix.get('summary',''))}\n")

        steps = fix.get("steps", [])
        for i, step in enumerate(steps, 1):
            print(f"  {col(C.GREEN, str(i)+'.')}  {step}")

        if cmd := fix.get("command"):
            print(f"\n  {dim('Run:')}")
            print(f"  {col(C.CYAN,'  $ ')}{col(C.WHITE, bold(cmd))}\n")

    if risks := result.get("related_risks"):
        section("RELATED RISKS", C.YELLOW)
        for r in risks:
            print(f"  {col(C.YELLOW,'⚡')}  {r}")

    if tip := result.get("prevention_tip"):
        section("PREVENTION", C.PURPLE)
        wrapped = textwrap.fill(tip, width=68, initial_indent="  💡  ", subsequent_indent="      ")
        print(f"\n{wrapped}\n")

    if similar := result.get("similar_failures"):
        section("SIMILAR PAST FAILURES (from RAG)", C.CYAN)
        for f in similar:
            score = f.get("score", 0)
            bar   = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            print(f"\n  {col(C.CYAN, bar)}  {score:.2f}  {col(C.YELLOW, f.get('error_type',''))}")
            print(f"  {dim('Cause:')}  {f.get('root_cause','')}")
            print(f"  {dim('Fix:')}    {col(C.GREEN, f.get('fix_applied',''))}")

    hr()
    print()


def print_stream(label: str, gen):
    """Print a streaming response with a label prefix."""
    print(f"\n  {col(C.PURPLE, bold(label))}\n")
    print("  ", end="", flush=True)
    col_idx = 0
    for token in gen:
        print(token, end="", flush=True)
        col_idx += len(token)
        if "\n" in token:
            print("  ", end="", flush=True)
            col_idx = 2
    print("\n")

def teach_flow(analyzer: PipelineAnalyzer):
    """Walk the user through recording a resolved failure."""

    print(f"\n{col(C.CYAN, bold('  📚  Teach PipelineIQ a New Resolution'))}")
    hr()
    print(dim("  This improves future analysis. Fill in what you found and fixed.\n"))

    pipeline_type = input(f"  {col(C.GREEN,'Pipeline type')} [github-actions/jenkins/gitlab/terraform/docker]: ").strip() or "github-actions"

    print(f"\n  {col(C.GREEN,'Paste the failure log')} (type END on a new line when done):")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)
    log_text = "\n".join(lines)

    if not log_text.strip():
        print(col(C.RED, "  No log provided. Cancelled."))
        return

    root_cause  = input(f"\n  {col(C.GREEN,'Root cause')} (one sentence): ").strip()
    error_type  = input(f"  {col(C.GREEN,'Error type')} (e.g. DependencyConflict): ").strip() or "Other"
    fix_applied = input(f"  {col(C.GREEN,'Fix applied')} (what you did): ").strip()
    cmd_raw     = input(f"  {col(C.GREEN,'Fix commands')} (comma-separated, or blank): ").strip()
    tags_raw    = input(f"  {col(C.GREEN,'Tags')} (comma-separated, e.g. npm,docker): ").strip()

    fix_commands = [c.strip() for c in cmd_raw.split(",")] if cmd_raw else []
    tags         = [t.strip() for t in tags_raw.split(",")] if tags_raw else []

    fid = analyzer.record_fix(log_text, pipeline_type, {
        "root_cause":   root_cause,
        "error_type":   error_type,
        "fix_applied":  fix_applied,
        "fix_commands": fix_commands,
        "tags":         tags,
    })

    print(f"\n  {col(C.GREEN,'✓')}  Recorded! Failure ID: {col(C.CYAN, fid)}")
    print(f"  Knowledge base now has {col(C.CYAN, str(analyzer.rag.store.count()))} entries.\n")



def interactive_loop(analyzer: PipelineAnalyzer):
    print(HELP_TEXT)

    chat_history    = []
    current_log     = ""
    current_ptype   = "github-actions"

    while True:
        try:
            prompt = input(f"{col(C.PURPLE,'pipelineiq')} {col(C.DIM,'›')} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{col(C.DIM,'  Goodbye.')}\n")
            break

        if not prompt:
            continue

        cmd = prompt.lower()

        # ── Built-in commands ─────────────────────────────────────────────────

        if cmd in ("exit", "quit", "q"):
            print(f"\n{col(C.DIM,'  Goodbye.')}\n")
            break

        elif cmd == "help":
            print(HELP_TEXT)

        elif cmd == "clear":
            chat_history = []
            current_log  = ""
            print(col(C.GREEN, "  ✓  Conversation cleared."))

        elif cmd == "stats":
            s = analyzer.stats()
            print(f"\n  {bold('PipelineIQ Stats')}")
            hr()
            print(f"  Knowledge base entries : {col(C.CYAN, str(s['knowledge_base_entries']))}")
            print(f"  LLM model              : {col(C.CYAN, s['llm_model'])}")
            print(f"  Ollama running         : {col(C.GREEN,'Yes') if s['ollama_running'] else col(C.RED,'No — run: ollama serve')}")
            print(f"  Available models       : {col(C.CYAN, ', '.join(s['available_models']) or 'none')}\n")

        elif cmd == "models":
            models = analyzer.llm.list_models()
            if models:
                print(f"\n  {bold('Available Ollama models:')}")
                for m in models:
                    marker = col(C.GREEN, " ✓") if m.split(":")[0] == analyzer.llm.model.split(":")[0] else "  "
                    print(f"  {marker}  {m}")
                print()
            else:
                print(col(C.RED, "  No models found. Run: ollama pull llama3\n"))

        elif cmd == "teach":
            teach_flow(analyzer)

        elif cmd.startswith("switch "):
            new_model = cmd.split(" ", 1)[1].strip()
            analyzer.llm.model = new_model
            print(col(C.GREEN, f"  ✓  Switched to model: {new_model}"))

        elif cmd == "analyze":
            # Interactive log paste
            print(f"\n  {col(C.CYAN, bold('Pipeline type'))} [github-actions/jenkins/gitlab/terraform/docker] (enter to keep '{current_ptype}'): ", end="")
            ptype = input().strip() or current_ptype
            current_ptype = ptype

            print(f"\n  {col(C.CYAN, bold('Paste your failure log'))} (type END on a new line when done):")
            lines = []
            while True:
                line = input()
                if line.strip().upper() == "END":
                    break
                lines.append(line)

            log_text = "\n".join(lines).strip()
            if not log_text:
                print(col(C.RED, "  No log provided.\n"))
                continue

            current_log  = log_text
            chat_history = []

            if not analyzer.llm.is_running():
                print(col(C.RED, "\n  ✗  Ollama is not running. Start it with: ollama serve\n"))
                continue

            print(f"\n  {col(C.DIM,'Analyzing...')}\n")
            result = analyzer.analyze(log_text, current_ptype)
            print_analysis(result)

            # Store the assistant summary in chat history for follow-ups
            summary = f"Analysis complete. Root cause: {result.get('root_cause','')}. Fix: {result.get('fix',{}).get('summary','')}"
            chat_history.append({"role": "assistant", "content": summary})

        # ── Load log from file ─────────────────────────────────────────────────

        elif cmd.startswith("analyze "):
            path = Path(cmd.split(" ", 1)[1].strip())
            if not path.exists():
                print(col(C.RED, f"  File not found: {path}\n"))
                continue
            log_text     = path.read_text()
            current_log  = log_text
            chat_history = []
            print(f"\n  {col(C.DIM,f'Loaded {path} ({len(log_text)} chars). Analyzing...')}\n")
            result = analyzer.analyze(log_text, current_ptype)
            print_analysis(result)
            summary = f"Analysis of {path}. Root cause: {result.get('root_cause','')}."
            chat_history.append({"role": "assistant", "content": summary})

        # ── Follow-up chat ─────────────────────────────────────────────────────

        else:
            if not analyzer.llm.is_running():
                print(col(C.RED, "\n  ✗  Ollama is not running. Start it with: ollama serve\n"))
                continue

            chat_history.append({"role": "user", "content": prompt})
            print()

            response_tokens = []
            print(f"  {col(C.PURPLE, bold('PipelineIQ'))}  ", end="", flush=True)
            for token in analyzer.chat(prompt, chat_history[:-1], current_log, current_ptype):
                print(token, end="", flush=True)
                response_tokens.append(token)
            full_response = "".join(response_tokens)
            chat_history.append({"role": "assistant", "content": full_response})
            print("\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PipelineIQ — Internal GPT for CI/CD failure intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python cli.py                              # Interactive mode
          python cli.py --log build.log              # Analyze a file
          python cli.py --log build.log --json       # Output raw JSON
          python cli.py --teach                      # Record a resolved failure
          python cli.py --stats                      # Show KB stats
          python cli.py --model codellama            # Use codellama model
        """)
    )
    parser.add_argument("--log",   help="Path to log file to analyze")
    parser.add_argument("--pipe",  default="github-actions", help="Pipeline type")
    parser.add_argument("--model", default="llama3",         help="Ollama model name")
    parser.add_argument("--json",  action="store_true",      help="Output raw JSON (no formatting)")
    parser.add_argument("--teach", action="store_true",      help="Record a resolved failure")
    parser.add_argument("--stats", action="store_true",      help="Show stats and exit")
    args = parser.parse_args()

    analyzer = PipelineAnalyzer(model=args.model)

    # Non-interactive modes
    if args.stats:
        s = analyzer.stats()
        print(json.dumps(s, indent=2))
        return

    if args.teach:
        teach_flow(analyzer)
        return

    if args.log:
        path = Path(args.log)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        log_text = path.read_text()
        result   = analyzer.analyze(log_text, args.pipe)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_analysis(result)
        return

    # Default: interactive loop
    interactive_loop(analyzer)


if __name__ == "__main__":
    main()
