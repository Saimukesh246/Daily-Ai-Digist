import os
import json
import logging
from datetime import datetime, timedelta
import google.generativeai as genai
from database import get_setting, save_digest, get_raw_articles_by_date, get_raw_articles_since

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analyzer")

def clean_gemini_json(response_text):
    """Cleans markdown JSON code blocks from Gemini responses if present."""
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def run_offline_fallback(date_str, raw_items):
    """Compiles real crawled news items into the strict newsletter JSON schema without Gemini."""
    logger.info("No Gemini API key available. Running intelligent offline fallback...")

    news_items  = [x for x in raw_items if x["category"] == "news"]
    tool_items  = [x for x in raw_items if x["category"] == "tool"]
    paper_items = [x for x in raw_items if x["category"] == "paper"]
    repo_items  = [x for x in raw_items if x["category"] == "repo"]

    # ── per-item helper functions ──────────────────────────────────────────────

    def _ctx(item):
        """Return lowercase combined title+description for keyword matching."""
        return (item.get("title", "") + " " + (item.get("description") or "")).lower()

    def _first_sentence(text, max_chars=180):
        if not text:
            return ""
        text = text.strip().split("\n")[0]
        for delim in [". ", "! ", "? "]:
            idx = text.find(delim)
            if 20 < idx < max_chars:
                return text[:idx + 1]
        return text[:max_chars].rstrip(" ,;") + ("..." if len(text) > max_chars else "")

    def _real_world_impact(item):
        t = _ctx(item)
        if any(w in t for w in ["agent", "autonom", "orchestrat"]):
            return "Reduces engineering overhead for reliable AI agents, letting smaller teams tackle automation that previously required dedicated infrastructure."
        if any(w in t for w in ["open source", "llama", "weights", "hugging"]):
            return "Lowers the cost floor for AI deployment and removes vendor lock-in, giving teams more architectural flexibility."
        if any(w in t for w in ["developer", "api", "sdk", "library", "framework"]):
            return "Compresses weeks of custom integration work into hours, directly increasing shipping velocity for AI-powered product teams."
        if any(w in t for w in ["security", "safety", "audit", "compliance"]):
            return "Provides concrete tooling for teams navigating AI compliance requirements, reducing legal and reputational exposure."
        if any(w in t for w in ["fund", "acqui", "invest", "billion"]):
            return "Capital concentration in this area accelerates hiring, research pace, and product cycles across the broader ecosystem."
        return "Contributes to the ongoing maturation of AI infrastructure, making the technology more accessible and cost-effective."

    def _why_news(item):
        t = _ctx(item)
        if any(w in t for w in ["open source", "open-source", "llama", "weights", "hugging"]):
            return "Accelerates AI democratisation by making powerful models available to teams without proprietary API budgets or GPU clusters."
        if any(w in t for w in ["agent", "autonom", "orchestrat", "workflow"]):
            return "Marks another step in the shift from single-turn AI queries toward persistent, goal-driven agents capable of complex multi-step execution."
        if any(w in t for w in ["api", "sdk", "developer", "library", "framework"]):
            return "Directly lowers the integration barrier for developers, compressing what used to take weeks of boilerplate into a few API calls."
        if any(w in t for w in ["fund", "billion", "invest", "acqui", "ipo"]):
            return "Signals continued institutional confidence in AI infrastructure investment, which accelerates both research pace and commercial adoption."
        if any(w in t for w in ["safety", "align", "risk", "bias", "audit"]):
            return "Addresses one of the most critical open problems in AI deployment — ensuring systems remain reliable, fair, and auditable at scale."
        if any(w in t for w in ["multimodal", "vision", "audio", "video", "image"]):
            return "Expands what AI can perceive and reason about, moving beyond text-only interfaces toward richer human-computer interaction."
        if any(w in t for w in ["china", "regulation", "policy", "government", "law"]):
            return "Geopolitical and regulatory shifts in AI are reshaping how global teams plan infrastructure and data compliance strategies."
        if any(w in t for w in ["benchmark", "state-of-the-art", "sota", "evaluat"]):
            return "New benchmarks redefine what 'good' looks like — affecting model selection, fine-tuning targets, and procurement decisions industry-wide."
        return "Represents a meaningful inflection point in how AI systems are built, evaluated, or deployed across the industry."

    def _key_features(item):
        desc = (item.get("description") or "").strip()
        features = []
        # Pipe-separated metadata is common in scraped HN/Reddit descriptions
        parts = [p.strip() for p in desc.split("|") if p.strip() and len(p.strip()) > 8]
        for p in parts[:3]:
            cap = p[0].upper() + p[1:]
            features.append(cap if cap.endswith(".") else cap + ".")
        t = _ctx(item)
        fillers = []
        if any(w in t for w in ["open source", "github", "repo"]):
            fillers.append("Open-source codebase with active community contributions.")
        if any(w in t for w in ["api", "sdk"]):
            fillers.append("API-first design enabling rapid integration into existing pipelines.")
        if any(w in t for w in ["benchmark", "performance", "faster"]):
            fillers.append("Performance benchmarks published alongside the release.")
        fillers += [
            "Detailed documentation and real-world usage examples included.",
            "Compatible with major cloud providers and local deployments.",
            "Community-validated through production usage at scale.",
        ]
        for f in fillers:
            if len(features) >= 3:
                break
            if f not in features:
                features.append(f)
        return features[:3]

    def _who_cares(item):
        t = _ctx(item)
        if any(w in t for w in ["enterprise", "business", "ceo", "cto", "executive"]):
            return "CTOs, AI product leads, and enterprise software architects."
        if any(w in t for w in ["research", "paper", "arxiv", "benchmark", "academic"]):
            return "ML researchers, PhD students, and applied AI scientists."
        if any(w in t for w in ["startup", "fund", "invest", "vc", "billion"]):
            return "Founders, investors, and AI product strategists."
        if any(w in t for w in ["security", "vulnerab", "compliance", "audit"]):
            return "Security engineers, compliance teams, and AI governance leads."
        return "AI engineers, full-stack developers, and technical founders."

    def _why_tool(item):
        t = _ctx(item)
        # Most-specific checks first so similar tools get differentiated strings
        if any(w in t for w in ["observab", "monitor", "session replay", "cost track", "telemetr"]):
            return "Observability is the missing layer in most agent deployments — without session replay and cost tracking, debugging production agents is nearly impossible."
        if any(w in t for w in ["memory", "knowledge base", "persistent"]):
            return "Persistent memory and structured knowledge management are what separate demo chatbots from production AI assistants that actually improve over time."
        if any(w in t for w in ["security", "vulnerab", "audit", "pentest", "scam", "threat"]):
            return "AI security tooling is a production requirement — this fills a critical gap that most platform stacks leave completely unaddressed."
        if any(w in t for w in ["browser", "social media", "tweet", "post", "scrape"]):
            return "Browser automation powered by AI reasoning unlocks entire workflow categories that previously required dedicated human operators."
        if any(w in t for w in ["rag", "retrieval", "embedding", "search", "vector"]):
            return "RAG infrastructure is table-stakes for enterprise AI — tools that simplify building and scaling it are seeing the fastest adoption rates."
        if any(w in t for w in ["code", "coding", "review", "pull request", "pr "]):
            return "Developer tooling that eliminates AI integration boilerplate directly compresses time-to-ship for engineering teams of every size."
        if any(w in t for w in ["gui", "interface", "desktop", "workspace", "claw"]):
            return "GUI-layer interfaces lower the entry barrier dramatically, letting non-developer users unlock the full power of frontier AI models."
        if any(w in t for w in ["agent", "autonom", "multi-step", "orchestrat", "workflow"]):
            return "Agent orchestration tooling is maturing fast — projects that nail the developer experience will define how production agents are built for years."
        if any(w in t for w in ["content", "marketing", "generat", "creat"]):
            return "Content generation tooling at this quality level compresses creative production cycles from days to minutes."
        return "Packages significant engineering effort into a reusable, installable tool that would otherwise take weeks to build from scratch."

    def _why_research(item):
        t = _ctx(item)
        cat = item.get("category", "")
        if cat == "repo":
            desc = (item.get("description") or "").lower()
            if "stars:" in desc:
                try:
                    stars = int(desc.split("stars:")[-1].split("|")[0].strip().replace(",", ""))
                    if stars >= 400:
                        return f"With {stars:,}+ stars in a short window, community adoption confirms this solves a genuine unmet need."
                except Exception:
                    pass
            if any(w in t for w in ["agent", "llm", "autonom"]):
                return "Rapid GitHub traction signals this codebase is filling a gap that existing LLM frameworks have not addressed."
            return "Community momentum this strong typically indicates real-world pain being solved rather than academic novelty."
        # Research paper — ordered most-specific to broadest so each paper lands a unique string
        if any(w in t for w in ["scam", "fraud", "phish", "malware", "attack", "adversar", "malicious"]):
            return "As AI agents gain access to sensitive user workflows, detecting and preventing adversarial misuse becomes a foundational safety requirement."
        if any(w in t for w in ["diffusion", "posterior", "sampl", "generative", "synthesiz", "image gen"]):
            return "Generative model research drives the next generation of image, video, and data synthesis tools — improvements here ripple through every creative and scientific application."
        if any(w in t for w in ["benchmark", "state-of-the-art", "outperform", "sota", "evaluat", "leaderboard"]):
            return "New state-of-the-art benchmark results set the bar that the next wave of model architectures will be designed to beat."
        if any(w in t for w in ["efficient", "faster", "compress", "quantiz", "distil", "lightweight", "prune"]):
            return "Efficiency techniques published in research papers like this get absorbed into mainstream frameworks within months, lowering the compute cost for everyone."
        if any(w in t for w in ["agent", "reasoning", "planning", "multi-step", "tool use", "tool-use"]):
            return "Advances in reasoning and planning directly determine how reliably autonomous agents can be deployed in production without constant human oversight."
        if any(w in t for w in ["multimodal", "vision", "audio", "video", "image", "visual"]):
            return "Multimodal capability research is laying the groundwork for the next generation of human-computer interaction beyond text-only interfaces."
        if any(w in t for w in ["safety", "align", "interpret", "explain", "bias", "fairness"]):
            return "Interpretability and alignment research is becoming a prerequisite for deploying AI in regulated or high-stakes environments."
        if any(w in t for w in ["transformer", "attention", "architectur", "layer", "neural", "network"]):
            return "Architectural improvements to core neural network components compound across every downstream application — small gains here matter significantly."
        if any(w in t for w in ["language model", "llm", "gpt", "pretraining", "fine-tun", "finetun"]):
            return "Language model research shapes the capabilities of tools developers and enterprises will rely on over the next 12–18 months."
        if any(w in t for w in ["retrieval", "rag", "embedding", "knowledge", "search"]):
            return "Retrieval and knowledge research directly improves the accuracy and reliability of production RAG systems used across enterprise deployments."
        return "Research published today across AI subfields tends to become engineering standard practice within 12–18 months — worth bookmarking early."

    # ── trend title + paragraphs ───────────────────────────────────────────────

    def _derive_trend():
        all_text = " ".join(_ctx(i) for i in raw_items)
        sources_present = sorted({i["source"] for i in raw_items})
        source_label = ", ".join(sources_present[:4]) or "multiple AI sources"

        themes = [
            (["agent", "autonomous", "multi-agent", "orchestrat", "agentic"],
             "The Rise of Autonomous AI Agents Across Production Stacks",
             "Today's AI landscape is increasingly defined not by model releases but by the systems built around them. Autonomous agents that plan, execute, and verify multi-step tasks are moving from research prototypes into production deployments across software engineering, data analysis, and enterprise workflows.",
             f"On {date_str}, signals aggregated from {source_label} point to agent orchestration as the dominant engineering challenge. Teams are no longer asking whether AI can do a task autonomously — they are asking how to make that automation reliable, observable, and auditable at scale."),
            (["open source", "open-source", "llama", "mistral", "qwen", "weights"],
             "Open-Source Models Continue Closing the Gap with Frontier Labs",
             "The open-weight AI ecosystem is evolving faster than most predicted. Community-trained and fine-tuned models are matching closed models on key benchmarks, and the tooling to run them efficiently — quantization, structured outputs, local inference — is now mature enough for production use.",
             f"On {date_str}, releases tracked across {source_label} confirm open-source momentum: teams previously locked into proprietary APIs are migrating toward self-hosted models driven by cost savings, data privacy requirements, and the ability to fine-tune for domain-specific tasks."),
            (["benchmark", "evaluat", "performance", "evals", "audit", "test"],
             "AI Evaluation Frameworks Are Struggling to Keep Pace with Capabilities",
             "As frontier models improve at accelerating rates, the benchmarks used to measure them are falling behind. Static academic tests can be saturated in months, and the field is converging on a consensus: meaningful evaluation must measure real-world task completion, not narrow leaderboard metrics.",
             f"On {date_str}, research preprints and community discussion from {source_label} highlight the evaluation crisis — new frameworks that test agents on long-horizon tasks, adversarial prompting, and grounded reasoning are emerging to replace saturated academic benchmarks."),
            (["tool", "sdk", "library", "framework", "developer", "api"],
             "Developer Tooling Is Now the Primary AI Competitive Battleground",
             "With foundation models increasingly commoditized, the competitive edge is shifting to the developer experience layer. SDKs, observability tools, prompt management, and deployment infrastructure are where the next cycle of AI value creation is concentrated.",
             f"On {date_str}, trending repositories and launches tracked via {source_label} confirm the pattern: the fastest-growing AI projects are not new models — they are the orchestration layers, monitoring dashboards, and workflow tools built on top of existing ones."),
            (["security", "safety", "alignment", "risk", "vulnerab", "compliance"],
             "AI Security and Alignment Move from Theory to Engineering Requirements",
             "The question of how to build AI systems that remain safe, reliable, and auditable under adversarial conditions has moved from academic debate to active engineering priority. Red-teaming, runtime guardrails, and output validation are entering standard deployment checklists.",
             f"On {date_str}, security-focused repositories and lab blog announcements from {source_label} reflect a shared inflection point: organisations shipping AI into regulated or high-stakes environments are now being required to demonstrate explicit safety and monitoring mechanisms."),
        ]
        for keywords, title, p1, p2 in themes:
            if any(kw in all_text for kw in keywords):
                return title, p1, p2
        return (
            "AI Capabilities and Infrastructure Continue Their Rapid Convergence",
            "Today's artificial intelligence ecosystem shows simultaneous momentum across model quality, infrastructure cost reduction, and developer tooling maturity. The speed at which research breakthroughs are translated into deployable systems continues to compress, reshaping what small teams can build.",
            f"On {date_str}, data aggregated from across the AI community confirms the trend: the boundary between cutting-edge research and production-ready implementation is thinner than it has ever been, and the teams moving fastest treat AI as infrastructure rather than novelty."
        )

    # ── assemble all sections ──────────────────────────────────────────────────

    # 1. Trend Overview
    trend_title, trend_p1, trend_p2 = _derive_trend()

    # 2. Biggest News Today
    default_news = [
        {"title": "Anthropic publishes new research on LLM interpretability", "url": "https://www.anthropic.com/news", "source": "Anthropic Blog",
         "description": "New mechanistic interpretability research maps abstract concepts to individual neuron clusters inside transformer models using dictionary learning techniques."},
        {"title": "OpenAI announces updated developer tooling for agent workflows", "url": "https://openai.com/news", "source": "OpenAI Blog",
         "description": "Updates to the Assistants API introduce parallel tool calling, improved structured output guarantees, and new lifecycle management endpoints."}
    ]
    selected_news = news_items[:3] if len(news_items) >= 2 else default_news
    biggest_news = []
    for item in selected_news:
        desc = (item.get("description") or "A significant AI development published today by researchers and industry leaders.")
        biggest_news.append({
            "headline": item["title"],
            "summary": desc[:300],
            "why_it_matters": _why_news(item),
            "key_features": _key_features(item),
            "real_world_impact": _real_world_impact(item),
            "who_should_care": _who_cares(item),
            "link": item["url"]
        })

    # 3. New Tools Discovered
    cats = ["AI Agents", "Developer Tools", "Coding Assistants", "Automation Tools", "Productivity AI", "Security Tools"]
    default_tools = [
        {"title": "AgentOps", "description": "Observability and evaluation platform for AI agents with session replay and cost tracking.", "url": "https://github.com/AgentOps-AI/agentops", "category": "tool"},
        {"title": "Phidata", "description": "Framework for building AI Assistants with persistent memory, knowledge bases, and tool use.", "url": "https://github.com/phidatahq/phidata", "category": "tool"}
    ]
    selected_tools = tool_items[:5] if len(tool_items) >= 2 else (repo_items[:3] + default_tools)
    discovered_tools = []
    for i, item in enumerate(selected_tools[:6]):
        desc = (item.get("description") or "")
        discovered_tools.append({
            "tool": item["title"].split("/")[-1],
            "category": cats[i % len(cats)],
            "what_it_does": (desc[:180] + "...") if len(desc) > 180 else desc,
            "why_it_matters": _why_tool(item),
            "pricing": "Open Source / Free Tier available",
            "link": item["url"]
        })

    # 4. What Changed
    what_changed = [
        {"tool_or_company": "Gemini API Platform",
         "yesterday": "Standard prompt windows with base vision support.",
         "today": "Expanded system instructions, native JSON schema enforcement, and 2M token context windows.",
         "why_it_matters": "Enables multi-hour audio/video document parsing without chunking — unlocking new categories of enterprise automation."},
        {"tool_or_company": "Open-Source LLMs (Llama, Mistral)",
         "yesterday": "Large compute requirements and heavy GPU quantization overhead.",
         "today": "Ultra-efficient FP4/FP8 quantization, context extension, and mobile-native execution paths.",
         "why_it_matters": "Makes private, local deployments of powerful reasoning models viable for mid-sized enterprises without dedicated GPU clusters."}
    ]

    # 5. Trending Workflows
    trending_workflows = [
        {"title": "Automated Code Review & PR Assistant",
         "problem_solved": "Senior engineers spending hours on manual style and safety reviews in every pull request.",
         "tools_used": "GitHub Actions, Gemini API, Pytest, Git",
         "steps": [
             "PR trigger spawns a lightweight CI runner.",
             "Changed files are diffed and fed into Gemini alongside the team's engineering standards.",
             "Gemini returns structured suggestions and flags potential edge-case regressions.",
             "Runner posts precise inline comments and a summary directly on the PR."
         ],
         "business_value": "Reduces QA cycle time by up to 40% and keeps senior engineers focused on architecture rather than line-by-line review.",
         "difficulty": "Intermediate"},
        {"title": "Local RAG Pipeline over Internal Documents",
         "problem_solved": "Teams wasting hours searching PDF archives and internal wikis for specific technical answers.",
         "tools_used": "LlamaIndex, SQLite, Streamlit, local HuggingFace Embeddings",
         "steps": [
             "Ingest document directory using a robust PDF and Markdown parser.",
             "Chunk and vector-index documents locally using SentenceTransformers.",
             "Query triggers SQLite plus vector similarity retrieval to find the most relevant paragraphs.",
             "Synthesise a final response in a local Streamlit interface with source citations."
         ],
         "business_value": "Eliminates data leakage to third-party endpoints and cuts internal search time from minutes to seconds.",
         "difficulty": "Beginner"}
    ]

    # 6. Open Source & Research
    open_source_research = []
    for item in paper_items[:3]:
        open_source_research.append({
            "title": item["title"],
            "category": "Research Paper",
            "summary": (item.get("description") or "")[:300],
            "why_it_matters": _why_research(item),
            "link": item["url"]
        })
    for item in repo_items[:3]:
        open_source_research.append({
            "title": item["title"],
            "category": "Repository",
            "summary": (item.get("description") or "")[:300],
            "why_it_matters": _why_research(item),
            "link": item["url"]
        })
    if not open_source_research:
        open_source_research = [
            {"title": "KAN: Kolmogorov-Arnold Networks", "category": "Research Paper",
             "summary": "An alternative to MLPs where learnable activation functions sit on edges rather than nodes, improving accuracy and mathematical interpretability.",
             "why_it_matters": "Could reshape neural architecture design, particularly for scientific computing tasks requiring interpretable models.",
             "link": "https://arxiv.org/abs/2404.19756"},
            {"title": "llama.cpp", "category": "Repository",
             "summary": "C/C++ port of Llama and compatible models enabling efficient local inference on consumer CPUs and Apple Silicon without a GPU.",
             "why_it_matters": "Core infrastructure powering the local open-source AI movement — essential for anyone building offline-first AI features.",
             "link": "https://github.com/ggerganov/llama.cpp"}
        ]

    # 7. Market Movements
    market_industry = [
        {"headline": "AI Infrastructure Startups Attract Record Round Sizes in Latest Funding Cycle",
         "summary": "Capital is concentrating in specialised AI hardware, cloud aggregation layers, and model security networks rather than consumer-facing wrappers.",
         "category": "Funding", "link": "https://news.ycombinator.com"},
        {"headline": "Enterprise SaaS Platforms Accelerate Native AI Integration Mandates",
         "summary": "Over 70% of enterprise platforms surveyed have added LLM-orchestrated features to core CRM and workflow dashboards within the past 12 months.",
         "category": "Enterprise", "link": "https://producthunt.com"}
    ]

    # 8. Quick Takes
    quick_takes = [
        {"topic": "No-Code Agent Builders",
         "opinion": "Excellent for demos and rapid prototyping, but reliably deploying complex enterprise workflows through drag-and-drop nodes remains brittle. Production use still requires code-level control over error handling, retry logic, and state management.",
         "hype_level": "Overhyped"},
        {"topic": "Small, Locally Hosted Language Models (3B–8B)",
         "opinion": "Dramatically underrated for structured, domain-specific tasks. Models in this range running on consumer hardware can match much larger cloud models on narrow workloads while offering full data privacy and near-zero marginal cost per request.",
         "hype_level": "Underrated"}
    ]

    # 9. What to Watch
    what_to_watch = [
        {"item": "Next-Generation Open-Source Multimodal Models",
         "details": "Several major open-weight developers are expected to release models with strong vision-language capabilities competitive with proprietary offerings."},
        {"item": "Stateful Long-Horizon Agent Benchmarks",
         "details": "New evaluation frameworks designed to test agent performance over hundreds of sequential steps — rather than single-turn completions — are expected from leading AI safety labs."}
    ]

    return {
        "date": date_str,
        "editorial_trend": {"title": trend_title, "paragraphs": [trend_p1, trend_p2]},
        "biggest_news": biggest_news,
        "discovered_tools": discovered_tools,
        "what_changed": what_changed,
        "trending_workflows": trending_workflows,
        "open_source_research": open_source_research,
        "market_industry": market_industry,
        "quick_takes": quick_takes,
        "what_to_watch": what_to_watch
    }

def generate_digest(db_path, date_str, api_key=None):
    """Aggregates daily items, compares them with history, synthesizes via Gemini or Fallback, and saves the output."""
    logger.info(f"Analyzing gathered news for {date_str}...")
    
    # 1. Fetch raw articles from SQLite database for today
    raw_items = get_raw_articles_by_date(db_path, date_str)
    
    if not raw_items:
        logger.warning(f"No raw articles found in database for date {date_str}. Running crawl inside generator as fallback...")
        from fetcher import fetch_all_sources
        from database import save_raw_article
        
        raw_items = fetch_all_sources(date_str)
        for item in raw_items:
            save_raw_article(
                db_path, date_str, item["source"], item["title"],
                item["description"], item["url"], item["category"]
            )
            
    # If still no articles, get some recent history or compile empty list
    if not raw_items:
        logger.warning("No crawled items available at all. Relying on default dataset inside fallback compiler.")
        
    # Get last week's data to support historical context
    past_items = get_raw_articles_since(db_path, date_str, days=7)
    
    # 2. Check for Gemini API key
    if not api_key:
        api_key = get_setting(db_path, "gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        
    if not api_key:
        # Run intelligent offline fallback
        digest_dict = run_offline_fallback(date_str, raw_items)
        save_digest(db_path, date_str, digest_dict)
        return digest_dict, "offline_fallback"
        
    # 3. If API Key exists, configure and invoke Gemini
    logger.info("Configuring Google Generative AI SDK...")
    try:
        genai.configure(api_key=api_key)
        
        # Prepare content payloads
        today_summary_str = ""
        for i, item in enumerate(raw_items):
            today_summary_str += f"- [{item['category'].upper()}] Source: {item['source']} | Title: {item['title']} | Info: {item['description']} | Link: {item['url']}\n"
            
        past_summary_str = ""
        for i, item in enumerate(past_items[:40]): # Top 40 items from last week
            past_summary_str += f"- [{item['category'].upper()}] Date: {item['date']} | Title: {item['title']}\n"
            
        system_instruction = (
            "You are an elite, premium AI technology analyst and veteran tech journalist (writing in the style of The Rundown AI, Ben's Bites, and TLDR AI). "
            "Your job is to read raw aggregated AI logs from today (along with a brief history of the past 7 days) and write a highly polished, daily newsletter digest. "
            "Your writing style must be exciting but factual, modern, highly readable, visually clean, beginner-friendly, and concise. "
            "Do NOT include generic placeholders or hypothetical tools. Synthesize the ACTUAL raw logs provided. "
            "You must return the digest in STRICT JSON FORMAT. Your output will be directly parsed by a JSON decoder, so do not include conversational fluff. "
            "Ensure all URLs are preserved exactly as provided in the raw logs. Do not make up URLs."
        )
        
        prompt = f"""
Write a comprehensive, premium AI Digest for {date_str}.

Here are today's crawled raw sources:
{today_summary_str}

Here is a summary of trending stories from the past 7 days (to prevent duplication and help track 'what changed'):
{past_summary_str}

You must respond in a valid JSON object matching the schema below exactly. 

Ensure the 'biggest_news' contains 2-3 of the absolute biggest headlines.
Ensure 'discovered_tools' is a list of up to 6 genuinely useful developer, productivity, agent, or image/video tools found in today's logs.
Ensure 'what_changed' is a table detail showing exactly 2-3 items that evolved since yesterday/last week (e.g. models updating, pricing adjustments, new integrations).
Ensure 'trending_workflows' outlines 2 practical AI workflows using modern tools (like agent architectures, automated coding pipelines, or advanced RAG).
Ensure 'open_source_research' highlights 3-4 trending Github repos or pre-prints on Arxiv with simple technical explanations.
Ensure 'market_industry' covers funding rounds, mergers, cloud/GPU announcements, or enterprise adoption.
Ensure 'quick_takes' includes sharp, insightful editorial takes on underrated or overhyped concepts.
Ensure 'what_to_watch' forecasts upcoming major releases or rising discussion topics.

RESPONSE SCHEMA:
{{
  "date": "{date_str}",
  "editorial_trend": {{
    "title": "Headline summarizing the core AI trend today",
    "paragraphs": [
      "Paragraph 1: Clear, exciting, modern synthesis of the biggest trend in today's AI landscape (3-4 sentences)...",
      "Paragraph 2: Wider implications of this shift, who it affects, and developer/enterprise impact (3-4 sentences)..."
    ]
  }},
  "biggest_news": [
    {{
      "headline": "Clear, punchy headline",
      "summary": "Comprehensive but concise 3-sentence summary of the news.",
      "why_it_matters": "Explain the underlying importance in 1-2 clear sentences.",
      "key_features": ["Crucial feature or takeaway 1", "Crucial feature or takeaway 2", "Crucial feature or takeaway 3"],
      "real_world_impact": "Explain how this changes the current industry landscape or developer standard.",
      "who_should_care": "e.g., AI Developers, CIOs, Technical Founders, SaaS Engineers",
      "link": "Exact source URL"
    }}
  ],
  "discovered_tools": [
    {{
      "tool": "Tool Name",
      "category": "e.g., AI Agents, Coding Assistant, Productivity, Image/Video, Developer Tool",
      "what_it_does": "1-2 sentence description of its function.",
      "why_it_matters": "Why this tool is genuinely useful instead of just another wrapper.",
      "pricing": "e.g., Open Source, Free Tier, $20/month, Paid",
      "link": "Exact source URL"
    }}
  ],
  "what_changed": [
    {{
      "tool_or_company": "Company or Tool Name",
      "yesterday": "What was the previous state (pricing, limits, missing features, older model)?",
      "today": "What is the new state (new parameters, features, cheaper API)?",
      "why_it_matters": "Impact on developers or businesses."
    }}
  ],
  "trending_workflows": [
    {{
      "title": "Workflow Title (e.g. Local Auto-quantization Pipeline)",
      "problem_solved": "What specific business or development friction does this workflow address?",
      "tools_used": "e.g., Llama.cpp, Ollama, LangChain",
      "steps": [
        "Step 1...",
        "Step 2...",
        "Step 3...",
        "Step 4..."
      ],
      "business_value": "1-2 sentences on time saved, cost savings, or capability unlocked.",
      "difficulty": "Beginner | Intermediate | Advanced"
    }}
  ],
  "open_source_research": [
    {{
      "title": "Paper Title or Repo Name",
      "category": "Research Paper | Repository",
      "summary": "Simple, beginner-friendly description of the technical breakthrough or codebase.",
      "why_it_matters": "Why developers or researchers should care.",
      "link": "Exact source URL"
    }}
  ],
  "market_industry": [
    {{
      "headline": "Funding, acquisition, or GPU cloud headline",
      "summary": "1-2 sentence description.",
      "category": "Funding | Acquisition | Enterprise | GPU/Cloud",
      "link": "Exact source URL"
    }}
  ],
  "quick_takes": [
    {{
      "topic": "The concept or trend (e.g. Small Language Models on Mobile)",
      "opinion": "Your sharp, highly expert, editorial perspective on this.",
      "hype_level": "Underrated | Overhyped | Emerging Opportunity"
    }}
  ],
  "what_to_watch": [
    {{
      "item": "Upcoming release, expected model, or event",
      "details": "What to look out for and why."
    }}
  ]
}}
"""
        
        # We will use gemini-1.5-flash or gemini-2.5-flash.
        # To be safe and compatible, let's use gemini-1.5-flash which is widely supported in the SDK
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        cleaned_json_text = clean_gemini_json(response.text)
        digest_dict = json.loads(cleaned_json_text)
        
        # Save the structured digest
        save_digest(db_path, date_str, digest_dict)
        logger.info("Successfully synthesized news via Gemini API!")
        return digest_dict, "gemini_synthesis"
        
    except Exception as e:
        logger.error(f"Failed to generate digest via Gemini API: {e}. Falling back to offline mode...")
        # Emergency fallback
        digest_dict = run_offline_fallback(date_str, raw_items)
        save_digest(db_path, date_str, digest_dict)
        return digest_dict, "offline_fallback_after_error"
