import Image from "next/image";
import { InstallTabs } from "@/components/InstallTabs";
import { LandingPageDemo } from "@/components/LandingPageDemo";
import { LandingPageNav } from "@/components/LandingPageNav";

function renderInlineCode(text: string) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, i) =>
    part.startsWith("`") && part.endsWith("`") ? (
      <code key={i} className="a-faq-code">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    ),
  );
}

function FAQItem({ q, a }: { q: string; a: string }) {
  return (
    <details className="a-faq-item">
      <summary className="a-faq-q">{q}</summary>
      <p className="a-faq-a">{renderInlineCode(a)}</p>
    </details>
  );
}

const features = [
  {
    num: "01",
    title: "Zero to eval",
    desc: "The coding agent bootstraps your evals to solve the cold-start problem. You review, not scaffold.",
  },
  {
    num: "02",
    title: "Checks gate the judge",
    desc: "Deterministic checks run before the LLM judge. If a check fails, no tokens are spent.",
  },
  {
    num: "03",
    title: "Trace everything",
    desc: "Auto-instruments Anthropic, OpenAI, and LangChain via OpenTelemetry (OTel).",
  },
  {
    num: "04",
    title: "Dataset-driven evals",
    desc: "Point at a JSONL file, each row becomes a run with its own trace and verdict. Re-run for variance stats, flaky detection, and anomaly flagging.",
  },
  {
    num: "05",
    title: "Structured judges",
    desc: "Define judge criteria in YAML with pass/fail definitions and few-shot examples. Reuse specs across scenarios for consistent grading.",
  },
  {
    num: "06",
    title: "No platform",
    desc: "uv or pip install, BYO API keys, all data stays local. Same CLI on your laptop and in CI.",
  },
];

const faqs = [
  {
    q: "What agents does kensa work with?",
    a: "Any Python agent that makes LLM calls. Auto-instrumentation covers Anthropic, OpenAI, and LangChain out of the box. Other providers work with manual OTel config.",
  },
  {
    q: "Do I need to modify my agent code?",
    a: "Two lines: `from kensa import instrument; instrument()`. Add before your SDK imports. kensa runs your agent in a subprocess and captures traces automatically. Auto instrumented by coding agents.",
  },
  {
    q: "Can I run kensa in CI?",
    a: "Yes. `kensa eval --format markdown` is all you need. Deterministic checks need no API keys. Add judge keys as secrets for LLM-judged criteria.",
  },
  {
    q: "Is kensa free?",
    a: "Yes, it is MIT licensed. The only cost is your LLM API calls for judge criteria, and that's optional.",
  },
];

const skills = [
  {
    name: "audit-evals",
    desc: "Assess readiness, identify testable behaviors, prepare the environment. The default entry point.",
  },
  {
    name: "generate-scenarios",
    desc: "Happy paths, edge cases, tool usage, error handling, cost bounds. One command.",
  },
  {
    name: "generate-judges",
    desc: "Binary pass/fail definitions with few-shot examples, ready to reuse across scenarios.",
  },
  {
    name: "validate-judge",
    desc: "Test judge accuracy against human labels. Iterates until TPR and TNR meet threshold.",
  },
  {
    name: "diagnose-errors",
    desc: "Categorize failures, identify patterns, recommend next action.",
  },
];

const cliCommands = [
  { cmd: "kensa init", desc: "Scaffold with an example agent" },
  { cmd: "kensa eval", desc: "run + judge + report in one shot" },
  { cmd: "kensa run", desc: "Execute scenarios, capture traces" },
  { cmd: "kensa judge", desc: "Deterministic checks + LLM judge" },
  { cmd: "kensa report", desc: "Terminal, markdown, JSON, or HTML output" },
  { cmd: "kensa analyze", desc: "Cost/latency stats + anomaly flagging" },
  { cmd: "kensa doctor", desc: "Pre-flight environment checks" },
];

const logos = [
  {
    src: "/claude-logo.png",
    alt: "Claude Code",
    tooltip: "Claude Code",
    w: 24,
    h: 24,
  },
  { src: "/cursor-logo.png", alt: "Cursor", tooltip: "Cursor", w: 24, h: 24 },
  {
    src: "/openai-logo.png",
    alt: "Codex CLI",
    tooltip: "Codex CLI",
    w: 20,
    h: 20,
    invert: true,
  },
  {
    src: "/gemini-logo.png",
    alt: "Gemini CLI",
    tooltip: "Gemini CLI",
    w: 20,
    h: 20,
  },
  {
    src: "/github-copilot-logo.svg",
    alt: "GitHub Copilot",
    tooltip: "GitHub Copilot",
    w: 22,
    h: 18,
    invert: true,
  },
  { src: "/kiro-logo.png", alt: "Kiro", tooltip: "Kiro", w: 20, h: 20 },
  {
    src: "/opencode-logo.png",
    alt: "OpenCode",
    tooltip: "OpenCode",
    w: 20,
    h: 20,
    invert: true,
  },
  { src: "/pi-logo.svg", alt: "Pi", tooltip: "Pi", w: 20, h: 20, pi: true },
];

export function LandingPage() {
  return (
    <>
      <LandingPageNav />

      <section className="a-hero">
        <div className="a-hero-inner">
          <div className="a-hero-copy">
            <h1 className="a-hero-title">
              The open source agent evals harness
            </h1>
            <p className="a-hook">
              Tell your coding agent to evaluate an agent and get a working
              eval suite in minutes. No platform needed.
            </p>
            <InstallTabs />

            <div className="a-logos-inline">
              <span className="a-logos-label">Works with</span>
              <div className="a-logos-row">
                {logos.map((l) => (
                  <span
                    key={l.alt}
                    className={`a-logo-icon${l.invert ? " a-logo-invert" : ""}${l.pi ? " a-logo-pi" : ""}`}
                    data-tooltip={l.tooltip}
                  >
                    <Image src={l.src} alt={l.alt} width={l.w} height={l.h} />
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="a-hero-demo">
            <LandingPageDemo />
          </div>
        </div>
      </section>

      <main className="a-content">
        <section className="a-section" id="features">
          <div className="a-section-header">
            <h2 className="a-section-title">How it works</h2>
            <p className="a-section-lead">
              Your coding agent reasons: it reads your codebase, identifies
              failure modes from traces, and writes scenarios. The CLI computes:
              it instruments, executes, judges, and reports. Skills orchestrate
              the workflow between them.
            </p>
          </div>

          <div className="a-features">
            {features.map((f) => (
              <div key={f.num} className="a-feature">
                <span className="a-feature-num">{f.num}</span>
                <h3 className="a-feature-title">{f.title}</h3>
                <p className="a-feature-desc">{f.desc}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="a-section" id="skills">
          <div className="a-section-header">
            <h2 className="a-section-title">Skills</h2>
            <p className="a-section-lead">
              Five skills take you from zero to eval, or from traces to targeted
              iteration.
            </p>
          </div>
          <div className="a-skills">
            {skills.map((s, i) => (
              <div key={s.name} className="a-skill">
                <div className="a-skill-marker">
                  <span className="a-skill-dot" />
                  {i < skills.length - 1 && <span className="a-skill-line" />}
                </div>
                <div className="a-skill-body">
                  <code className="a-skill-name">/{s.name}</code>
                  <p className="a-skill-desc">{s.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="a-section a-section--solid" id="cli">
          <div className="a-section-header">
            <h2 className="a-section-title">
              CLI <span className="a-badge">Python 3.10+</span>
            </h2>
            <p className="a-section-lead">
              Works standalone for CI and local iteration. Checks run before the
              judge, so obvious failures stop early without spending tokens.
            </p>
          </div>
          <div className="a-cli">
            {cliCommands.map((c) => (
              <div key={c.cmd} className="a-cli-row">
                <code className="a-cli-cmd">{c.cmd}</code>
                <span className="a-cli-desc">{c.desc}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="a-section a-section--solid" id="faq">
          <div className="a-section-header">
            <h2 className="a-section-title">FAQ</h2>
          </div>
          <div className="a-faq">
            {faqs.map((f) => (
              <FAQItem key={f.q} q={f.q} a={f.a} />
            ))}
          </div>
        </section>
      </main>

      <footer className="a-footer">
        <div className="a-footer-inner">
          <div className="a-footer-left">
            <span className="a-footer-brand">
              kensa <span className="a-footer-kanji">検査</span>
            </span>
            <p className="a-footer-etymology">
              <span className="a-footer-reading">/ken·sa/</span>{" "}
              <span className="a-footer-def">
                — to inspect before releasing to the world.
              </span>
            </p>
          </div>
          <div className="a-footer-links">
            <a href="/llms.txt" target="_blank" rel="noopener noreferrer">llms.txt</a>
            <a href="/docs/changelog">Changelog</a>
            <a href="https://discord.gg/n77EqxUH">Discord</a>
            <a href="https://x.com/kensa_sh">X</a>
          </div>
        </div>
      </footer>
    </>
  );
}
