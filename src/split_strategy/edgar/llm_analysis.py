"""
LLM analysis for EDGAR filings.
"""
import os
import json
import re

# Extensive Keyword List for Initial Filtering
SPLIT_KEYWORDS = [
    r"reverse\s+stock\s+split",
    r"reverse\s+split",
    r"stock\s+consolidation",
    r"share\s+consolidation",
    r"consolidation\s+of\s+shares",
    r"share\s+combination",
    r"combining\s+outstanding\s+shares",
    r"exchange\s+ratio",
    r"split\s+ratio",
    r"one-for-[0-9]+",
    r"1-for-[0-9]+",
    r"1\s+for\s+[0-9]+",
    r"1:[0-9]+"
]

def get_analysis_prompt(company: str, context: str, filing_date: str) -> str:
    """
    Generates the LLM prompt for analyzing filing text.
    """
    return f"""
    You are a financial analyst specializing in Corporate Actions.
    Analyze this SEC filing text for {company} (Filed on {filing_date}).
    
    GOAL: Determine if this filing confirms a **FUTURE** REVERSE STOCK SPLIT.
    
    CRITICAL RULES:
    1. **Effective Date Check**:
       - Extract the "effective_date" of the reverse split.
       - If the effective date is BEFORE or ON the filing date ({filing_date}), this is a PAST event.
       - If the text says the split "became effective" or "took effect", it is a PAST event.
       
    2. **Definitive Decision vs. Proposal/Discussion**:
       - A **Definitive Split Announcement** means the Board has officially approved and decided to execute the reverse stock split. The split IS definitely happening and the board is announcing its execution date/ratio.
       - A **Proposal/Discussion/Authorization Request** means the company is seeking shareholder approval, discussing a potential split at an upcoming meeting, proposing a range of ratios for future discretion, or seeking voting authority to allow the board to execute a split *if* they decide to later. The split is NOT yet a definitive decision to execute.
       
    3. **Confidence Score**:
       - "High": Explicit confirmation that the split **IS definitely decided/happening** (definitive announcement of execution) with a specific ratio and date.
       - "Medium": Confirmed definitive split that is definitely going to happen, but missing exact date or exact ratio.
       - "Low": A mere proposal, meeting discussion, request for voting authority/discretion, or a PAST/ALREADY EFFECTIVE split.
    
    Text Context:
    {context}
    
    Return a JSON object with:
    - "is_reverse_split": boolean (True if it IS a reverse split document)
    - "is_future_split": boolean (True ONLY if effective date > {filing_date} or explicitly stated as future)
    - "is_definitive_split_announcement": boolean (True ONLY if the split is a definitive decision to execute. False if it is a proposal, discussion, meeting authorization request, or seeking discretionary authority to execute a split in the future.)
    - "effective_date": string (YYYY-MM-DD or "Unknown")
    - "ratio": string (e.g., "1-for-10")
    - "rounding_up": boolean (True if fractional shares are rounded UP)
    - "confidence": "High", "Medium", "Low" (Downgrade to Low if is_future_split is False or if it is a mere proposal/discussion/authorization request rather than a definitive decision to execute)
    - "summary": One sentence summary (e.g. "1-for-10 split effective on [Date]").
    """

def check_keywords_extensive(text: str) -> bool:
    """Check for any split-related keywords"""
    for pattern in SPLIT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def analyze_with_llm(text: str, company: str, filing_date: str, openai_api_key: str = None) -> dict:
    """Use OpenAI to analyze the filing text"""
    if not openai_api_key:
        return {"summary": "LLM API Key missing", "confidence": "Low", "rounding_up": None, "effective_date": "Unknown", "ratio": "Unknown"}
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)
        
        # Truncate text to avoid token limits.
        # Strategy: find keyword matches and take context.
        matches = []
        for pattern in SPLIT_KEYWORDS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                start = max(0, m.start() - 1000)
                end = min(len(text), m.end() + 1000)
                matches.append(text[start:end])
                if len(matches) > 3: break 
        
        context = "\n...\n".join(matches) if matches else text[:5000]

        prompt = get_analysis_prompt(company, context, filing_date)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        return json.loads(response.choices[0].message.content)
        
    except Exception as e:
        print(f"LLM Error: {e}")
        return {"summary": f"Error analyzing: {str(e)}", "confidence": "Error"}
