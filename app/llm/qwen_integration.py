"""
Qwen LLM Integration Module

Provides wrapper functions for interacting with Alibaba's Qwen models
through the OpenAI-compatible DashScope API (QwenCloud endpoint).

Uses OpenAI Python client with DashScope compatible endpoint.
"""

import json
import os
import re
from typing import Dict, Any, Optional

from loguru import logger
from openai import OpenAI
from config.settings import settings

# Global client - initialized on first use
_qwen_client: Optional[OpenAI] = None


def get_qwen_client() -> OpenAI:
    """Get or create OpenAI client for Qwen."""
    global _qwen_client
    
    if _qwen_client is None:
        if not settings.dashscope_api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY environment variable not set. "
                "Please set your Alibaba Qwen API key."
            )
        
        _qwen_client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_endpoint or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
        
        logger.info(f"✓ Qwen OpenAI client initialized")
        logger.info(f"✓ Model: {settings.qwen_model}")
        logger.info(f"✓ Endpoint: {settings.dashscope_endpoint or 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'}")
        logger.debug(f"  API Key starts with: {settings.dashscope_api_key[:20]}...")
    
    return _qwen_client


def initialize_qwen():
    """Initialize Qwen API with credentials from settings."""
    if not settings.dashscope_api_key:
        raise ValueError(
            "DASHSCOPE_API_KEY environment variable not set. "
            "Please set your Alibaba Qwen API key."
        )
    
    client = get_qwen_client()
    logger.info(f"✓ Qwen initialized with model: {settings.qwen_model}")
    logger.info(f"✓ API Key length: {len(settings.dashscope_api_key)} chars")
    logger.debug(f"  API Key starts with: {settings.dashscope_api_key[:20]}...")


def call_qwen_with_structured_output(
    system_prompt: str,
    user_message: str,
    response_schema: Dict[str, Any],
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call Qwen with a structured output request.
    
    Args:
        system_prompt: System message defining the assistant's role
        user_message: User query/prompt
        response_schema: Expected JSON schema for response
        temperature: Sampling temperature (0.0-2.0), defaults to settings
        top_p: Nucleus sampling parameter, defaults to settings
        model: Model to use, defaults to settings.qwen_model
        
    Returns:
        Parsed JSON response matching the schema
        
    Raises:
        ValueError: If API key not set
        RuntimeError: If API call fails or response cannot be parsed
    """
    temperature = temperature or settings.qwen_temperature
    top_p = top_p or settings.qwen_top_p
    model = model or settings.qwen_model
    
    # Prepare the prompt with schema instructions
    schema_instruction = f"""
You must respond with ONLY valid JSON matching this schema:
{json.dumps(response_schema, indent=2)}

Do not include any text outside the JSON response.
"""
    
    full_user_message = f"{user_message}\n\n{schema_instruction}"
    
    try:
        client = get_qwen_client()
        
        logger.info(f"Calling Qwen ({model}) with structured output request")
        logger.debug(f"User message: {user_message[:200]}...")
        logger.debug(f"Temperature: {temperature}, Top-p: {top_p}")
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": full_user_message,
                }
            ],
            temperature=temperature,
            top_p=top_p,
            timeout=120.0,  # 120s timeout for complex requests
        )
        
        logger.debug(f"Qwen response received: {response.choices[0].message.content[:300]}...")
        
        # Extract the response text
        response_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        try:
            # Try to extract JSON if there's surrounding text
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            
            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                logger.debug(f"Extracted JSON: {json_str[:200]}...")
            else:
                json_str = response_text
                logger.debug(f"Using full response as JSON: {json_str[:200]}...")
            
            # Validate JSON is complete before parsing
            if not json_str.strip().endswith("}"):
                logger.warning(f"JSON doesn't end with }}, attempting to fix: {json_str[-50:]}")
                # Try to find the last complete object
                brace_count = 0
                last_complete_pos = -1
                for i, char in enumerate(json_str):
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            last_complete_pos = i + 1
                
                if last_complete_pos > 0:
                    json_str = json_str[:last_complete_pos]
                    logger.debug(f"Truncated to last complete object at position {last_complete_pos}")
            
            parsed_response = json.loads(json_str)
            logger.debug(f"Parsed response: {parsed_response}")
            return parsed_response
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Qwen JSON response: {response_text[:500]}")
            logger.error(f"JSON parsing error: {str(e)}")
            raise RuntimeError(
                f"Failed to parse Qwen response as JSON: {str(e)}"
            )
        
    except Exception as e:
        logger.error(f"Qwen API call failed: {str(e)}")
        raise RuntimeError(f"Qwen API error: {str(e)}")


def call_qwen_for_triage(user_input: str, conversation_history: list = None) -> Dict[str, Any]:
    """
    Use Qwen to perform triage/routing of user query.

    Args:
        user_input: User query to route
        conversation_history: Prior chat turns for disambiguation

    Returns:
        Dict with keys: ticker, routing_path, confidence, reasoning
    """
    from app.prompts.system_prompts import TRIAGE_PROMPT, SYSTEM_MESSAGE_RESEARCH

    schema = {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "Stock ticker symbol or empty string"
            },
            "routing_path": {
                "type": "string",
                "enum": ["research", "direct_trade", "general_q", "portfolio"],
                "description": "Routing path for the query"
            },
            "trade_action": {
                "anyOf": [
                    {"type": "string", "enum": ["BUY", "SELL"]},
                    {"type": "null"}
                ],
                "description": "Trade action (BUY/SELL) for direct_trade path, null for others"
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence score of routing"
            },
            "reasoning": {
                "type": "string",
                "description": "Explanation of routing decision"
            }
        },
        "required": ["ticker", "routing_path", "confidence", "reasoning"]
    }

    # Prepend recent conversation context so triage can resolve follow-ups
    # (e.g. "What about the bear case?" → know it's about the last ticker)
    history = conversation_history or []
    
    # Use FULL conversation history (no truncation) to preserve ticker context
    # Include both user messages and assistant responses for complete context
    recent = []
    for turn in history[-10:]:  # Use all passed turns (up to 5 pairs = 10 messages)
        role = turn.get("role", "")
        content = turn.get("content", "").strip()
        
        # Include user messages in full, and assistant routing_decision metadata if present
        if role == "user" and content:
            recent.append(f"User: {content}")
        elif role == "assistant" and content:
            # For assistant messages, include key context (first 200 chars) + any routing decision
            summary = content[:200] if len(content) > 200 else content
            routing_decision = turn.get("routing_decision", "")
            if routing_decision:
                recent.append(f"Assistant (route: {routing_decision}): {summary}...")
            else:
                recent.append(f"Assistant: {summary}...")
    
    conversation_context = " | ".join(recent) if recent else "(no prior context)"

    formatted_prompt = TRIAGE_PROMPT.format(user_input=user_input, conversation_context=conversation_context)

    response = call_qwen_with_structured_output(
        system_prompt=SYSTEM_MESSAGE_RESEARCH,
        user_message=formatted_prompt,
        response_schema=schema,
        model=settings.qwen_routing_model,  # Use configured routing model for fast intent classification
    )

    # ────────────────────────────────────────────────────────────────────
    # POST-PROCESS: Extract ticker explicitly from current query if Qwen misses it
    # ────────────────────────────────────────────────────────────────────
    import re
    
    logger.info(f"[TRIAGE DEBUG] Current user query: {user_input}")
    logger.info(f"[TRIAGE DEBUG] Qwen extracted ticker: {response.get('ticker')}")
    
    # Pattern: look for uppercase 1-5 letter ticker symbols in current query
    # Prioritize: explicit symbols like $TSLA, TSLA stock, buy TSLA, TSLA company, etc.
    ticker_pattern = r'\b([A-Z]{1,5})\b'
    matches = re.findall(ticker_pattern, user_input)
    logger.info(f"[TRIAGE DEBUG] Regex found candidates: {matches}")
    
    # Filter for likely tickers (exclude common words like "A", "I", "THE", "BUY", "SELL", etc.)
    common_words = {"A", "I", "THE", "AND", "BUY", "SELL", "HOLD", "YES", "NO", "FOR", "NOT", "ALL", "NEW", "OLD", "HOW", "WHY", "CAN", "MAY", "WILL", "SHOULD", "WOULD", "COULD", "WAS", "ARE", "BEEN", "HAS", "GET", "GIVE", "TAKE", "MAKE", "DO", "GO", "COME"}
    explicit_tickers = [t for t in matches if t not in common_words and len(t) <= 5]
    logger.info(f"[TRIAGE DEBUG] After filtering common words: {explicit_tickers}")
    
    # If current query has an explicit ticker, ALWAYS use it (override Qwen if necessary)
    if explicit_tickers:
        extracted_ticker = explicit_tickers[0]
        if response.get("ticker") != extracted_ticker:
            logger.info(f"[TICKER OVERRIDE] Overriding Qwen ticker '{response.get('ticker')}' with explicit query ticker '{extracted_ticker}'")
            response["ticker"] = extracted_ticker
            response["reasoning"] = f"Explicit ticker {extracted_ticker} found in current query. {response.get('reasoning', '')}"
        else:
            logger.info(f"[TICKER MATCH] Qwen correctly extracted {extracted_ticker}")
    
    logger.info(f"[TRIAGE DEBUG] Final response ticker: {response.get('ticker')}")

    return response


def call_qwen_for_research(
    ticker: str,
    price: float,
    market_cap: float,
    pe_ratio: float,
    change_pct: float,
) -> str:
    """
    Use Qwen to generate research report.
    
    Args:
        ticker: Stock ticker
        price: Current stock price
        market_cap: Market cap in billions
        pe_ratio: P/E ratio
        change_pct: 52-week change percentage
        
    Returns:
        Research report in markdown format
    """
    from app.prompts.system_prompts import (
        RESEARCH_PROMPT,
        SYSTEM_MESSAGE_RESEARCH
    )
    
    formatted_prompt = RESEARCH_PROMPT.format(
        ticker=ticker,
        price=price,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        change_pct=change_pct,
    )
    
    try:
        client = get_qwen_client()
        logger.info(f"Calling Qwen for research report on {ticker}")
        
        response = client.chat.completions.create(
            model=settings.qwen_model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_MESSAGE_RESEARCH,
                },
                {
                    "role": "user",
                    "content": formatted_prompt,
                }
            ],
            temperature=settings.qwen_temperature,
            top_p=settings.qwen_top_p,
            timeout=30.0,
        )
        
        report = response.choices[0].message.content.strip()
        
        # Sanitize response: fix encoding issues and duplicates
        # Step 1: Replace Unicode asterisks with ASCII
        report = report.replace("∗∗", "**")  # Unicode double asterisk to markdown bold
        report = report.replace("∗", "*")     # Unicode single asterisk to ASCII
        
        # Step 2: Remove corrupted duplicate patterns
        # Fix corrupted bold patterns: "word∗∗word∗∗" → "**word**"
        report = re.sub(r'(\w+)\*\*\1\*\*', r'**\1**', report, flags=re.IGNORECASE)
        
        # Step 3: Clean up lines with obvious duplication
        lines = report.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # Skip empty lines at start
            if not line.strip():
                if cleaned_lines:  # Keep one empty line between content
                    cleaned_lines.append(line)
                continue
            
            # Check if line has obvious duplication pattern
            # If a line contains text repeated exactly, keep only first occurrence
            parts = line.split('**')
            if len(parts) > 2:
                # Rebuild line with proper markdown
                # This handles cases like "text**text**text" → "text**text**"
                reconstructed = parts[0].strip()
                for i in range(1, len(parts), 2):
                    if i < len(parts):
                        bold_text = parts[i].strip()
                        if bold_text and (i + 1 >= len(parts) or parts[i + 1].strip()):
                            reconstructed += f"**{bold_text}**"
                            if i + 1 < len(parts):
                                next_part = parts[i + 1].strip()
                                if next_part and not next_part.startswith(("**", "*")):
                                    reconstructed += f" {next_part}"
                line = reconstructed
            
            cleaned_lines.append(line)
        
        report = "\n".join(cleaned_lines)
        
        # Step 4: Remove excessive blank lines (more than 2 consecutive)
        report = re.sub(r'\n\n\n+', '\n\n', report)
        
        logger.info(f"Research report generated for {ticker}")
        logger.debug(f"Report length (sanitized): {len(report)} characters")
        return report
        
    except Exception as e:
        logger.error(f"Qwen research call failed: {str(e)}")
        raise RuntimeError(f"Qwen research error: {str(e)}")


def call_qwen_persona(
    system_prompt: str,
    user_message: str,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> str:
    """
    Call Qwen as a specific persona and return a plain-text (markdown) response.

    Used by the multi-agent Investment Committee to run distinct analyst personas
    (Bull Analyst, Bear Auditor, Portfolio Director) through a structured debate.

    Args:
        system_prompt: Persona-defining system message
        user_message: The turn prompt (data + running debate transcript)
        temperature: Sampling temperature, defaults to settings
        top_p: Nucleus sampling parameter, defaults to settings

    Returns:
        The persona's argument as a markdown string

    Raises:
        RuntimeError: If the API call fails
    """
    temperature = temperature if temperature is not None else settings.qwen_temperature
    top_p = top_p if top_p is not None else settings.qwen_top_p

    try:
        client = get_qwen_client()
        logger.debug(f"Persona call: {len(user_message)} chars of context")
        
        # Log message size for diagnostics
        message_size = len(system_prompt) + len(user_message)
        logger.info(f"[PERSONA] System prompt: {len(system_prompt)} chars | User message: {len(user_message)} chars | Total: {message_size} chars")
        
        if message_size > 100_000:
            logger.warning(f"[PERSONA] ⚠️ LARGE CONTEXT: {message_size} chars — may cause timeout or high token usage")
        
        # Use streaming to show real-time progress and collect chunks
        logger.info("[PERSONA] Streaming response...")
        text_chunks = []
        chunk_count = 0
        
        response = client.chat.completions.create(
            model=settings.qwen_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            top_p=top_p,
            timeout=90.0,  # Increased for complex debate rounds with streaming
            stream=True,  # Enable streaming
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                text_chunks.append(delta)
                chunk_count += 1
                
                # DIAGNOSTIC: Log suspicious chunks (short, no spaces, special chars)
                if len(delta) <= 5 and delta.strip() and not delta.startswith(" "):
                    logger.debug(f"[PERSONA] Chunk #{chunk_count}: [{repr(delta)}] (len={len(delta)})")
                
                # Log every 10 chunks to show progress without log spam
                if chunk_count % 10 == 0:
                    logger.debug(f"[PERSONA] Streamed {chunk_count} chunks so far...")
        
        text = "".join(text_chunks).strip()
        logger.info(f"[PERSONA] Stream complete ({chunk_count} chunks, {len(text)} chars)")
        
        # DIAGNOSTIC: Check for concatenation issues in the result
        if "the " in text and re.search(r'[a-z]{10,}[A-Z]', text):
            logger.warning(f"[PERSONA] ⚠️  Detected potential word concatenation in output")
        
        # Normalize Unicode asterisks that Qwen occasionally emits
        text = text.replace("∗∗", "**").replace("∗", "*")
        text = re.sub(r'\n\n\n+', '\n\n', text)
        return text
    except Exception as e:
        logger.error(f"Qwen persona call failed: {str(e)}")
        raise RuntimeError(f"Qwen persona error: {str(e)}")


def call_qwen_for_general_qa(user_input: str, conversation_history: list = None) -> str:
    """
    Use Qwen to answer general financial questions.

    Args:
        user_input: User question
        conversation_history: Prior turns [{role, content}, ...] for follow-up context

    Returns:
        Answer in markdown format
    """
    from app.prompts.system_prompts import (
        GENERAL_QA_PROMPT,
        SYSTEM_MESSAGE_QA
    )

    formatted_prompt = GENERAL_QA_PROMPT.format(user_input=user_input)

    try:
        client = get_qwen_client()
        logger.info("Calling Qwen for general Q&A")

        # Build messages: system → minimal history context → current user query
        history = conversation_history or []
        # Keep ONLY last 1 turn (user question) for context, heavily truncated
        recent_history = history[-1:] if len(history) > 0 else []

        messages = [{"role": "system", "content": SYSTEM_MESSAGE_QA}]
        for turn in recent_history:
            role = turn.get("role", "user")
            content = turn.get("content", "")[:200]  # Truncate severely
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": formatted_prompt})

        response = client.chat.completions.create(
            model=settings.qwen_model,
            messages=messages,
            temperature=settings.qwen_temperature,
            top_p=settings.qwen_top_p,
            timeout=30.0,
        )

        answer = response.choices[0].message.content.strip()

        # Sanitize response: fix encoding issues and duplicates (same as research)
        # Step 1: Replace Unicode asterisks with ASCII
        answer = answer.replace("∗∗", "**")  # Unicode double asterisk to markdown bold
        answer = answer.replace("∗", "*")     # Unicode single asterisk to ASCII

        # Step 2: Fix corrupted duplicate patterns
        answer = re.sub(r'(\w+)\*\*\1\*\*', r'**\1**', answer, flags=re.IGNORECASE)

        # Step 3: Remove excessive blank lines
        answer = re.sub(r'\n\n\n+', '\n\n', answer)

        logger.info("General Q&A response generated")
        logger.debug(f"Answer length (sanitized): {len(answer)} characters")
        return answer

    except Exception as e:
        logger.error(f"Qwen QA call failed: {str(e)}")
        raise RuntimeError(f"Qwen QA error: {str(e)}")
