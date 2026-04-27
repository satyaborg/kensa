import Image from "next/image";
import Link from "next/link";
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
    desc: "Ask your coding agent to inspect the codebase and draft the first scenarios. You review evals instead of starting from a blank file.",
  },
  {
    num: "02",
    title: "Runs become traces",
    desc: "Kensa captures LLM calls, tool use, tokens, cost, and latency while your agent runs each scenario.",
  },
  {
    num: "03",
    title: "Checks gate judges",
    desc: "Assertions run before LLM judges, catching obvious regressions without spending tokens.",
  },
  {
    num: "04",
    title: "Ship with evidence",
    desc: "Get verdicts, traces, cost, latency, and failure details in terminal, Markdown, JSON, or HTML.",
  },
];

const faqs = [
  {
    q: "What agents does kensa work with?",
    a: "Any Python agent that makes LLM calls. Auto-instrumentation covers Anthropic, OpenAI, and LangChain out of the box. Other providers work with manual OTel config.",
  },
  {
    q: "Do I need to modify my agent code?",
    a: "No. kensa auto-instruments your agent at startup. Zero code changes needed.",
  },
  {
    q: "Can I run kensa in CI?",
    a: "Yes. `kensa eval --format markdown` is all you need. Deterministic checks need no API keys. Add judge keys as secrets for LLM-judged criteria.",
  },
  {
    q: "Can I drive kensa from an MCP client?",
    a: "Yes. In Claude Code, `claude mcp add kensa -- uvx kensa-mcp` registers the stdio server — uvx fetches the `kensa-mcp` package from PyPI on first run, no pre-install needed. Every CLI action is a tool, and runs, scenarios, and judges are readable as resources under `kensa://`.",
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
  { cmd: "kensa generate", desc: "Synthesize scenarios from captured traces" },
  { cmd: "kensa eval", desc: "run + judge + report in one shot" },
  { cmd: "kensa run", desc: "Execute scenarios, capture traces" },
  { cmd: "kensa judge", desc: "Deterministic checks + LLM judge" },
  { cmd: "kensa report", desc: "Terminal, markdown, JSON, or HTML output" },
  { cmd: "kensa analyze", desc: "Cost/latency stats + anomaly flagging" },
  { cmd: "kensa doctor", desc: "Pre-flight environment checks" },
  { cmd: "kensa mcp", desc: "Serve kensa over MCP for LLM clients" },
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
            <Link href="/docs/mcp-server" className="a-badge">
              <span className="a-badge-tag">NEW</span>
              MCP server available
            </Link>
            <h1 className="a-hero-title">Zero to evals in minutes.</h1>
            <p className="a-hook">
              Your coding agent drafts evals. You approve. Kensa instruments and
              runs them.
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
        </div>
      </section>

      <section className="a-demo-section">
        <div className="a-demo-section-inner">
          <LandingPageDemo />
        </div>
      </section>

      <main className="a-content">
        <section className="a-section" id="features">
          <div className="a-section-header">
            <h2 className="a-section-title">How it works</h2>
            <p className="a-section-lead">
              Kensa turns agent behavior into repeatable evals: scenarios in,
              traces captured, checks run, reports out.
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

          <p className="a-features-outro">
            Each run leaves traces that kensa can{" "}
            <Link href="/docs/cli#kensa-generate">
              turn into sharper scenarios
            </Link>
            .
          </p>
        </section>

        <section className="a-section" id="skills">
          <div className="a-section-header">
            <h2 className="a-section-title">Skills</h2>
            <p className="a-section-lead">
              {skills.length} skills take you from zero to eval, or from traces
              to targeted iteration.
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
              CLI{" "}
              <span className="a-badge">
                <span className="a-badge-tag">PY</span>3.10+
              </span>
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
                to inspect before releasing to the world.
              </span>
            </p>
          </div>
          <div className="a-footer-links">
            <a href="/llms.txt" target="_blank" rel="noopener noreferrer">
              llms.txt
            </a>
            <a href="https://x.com/kensa_sh">X</a>
          </div>
        </div>
      </footer>
    </>
  );
}
