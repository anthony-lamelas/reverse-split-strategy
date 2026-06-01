"""
Main dashboard logic for Streamlit.
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys

# Import from package
from ..database import get_collection, REVERSE_SPLITS_COLLECTION, EDGAR_COLLECTION, EARLY_WARNINGS_COLLECTION
from ..edgar.client import get_cik_mapping_with_names
from ..edgar.processing import process_reverse_split_with_edgar
from ..edgar.utils import parse_date
from ..analysis.returns import get_stock_price_data_around_split, get_current_price
from .utils import check_rounding_flag, get_rounding_filings, has_edgar_data

# Cache CIK mapping
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_cik_mappings():
    """Get CIK mappings (cached)"""
    return get_cik_mapping_with_names()

@st.cache_data(ttl=600)  # Cache for 10 minutes
def fetch_recent_splits():
    """Fetch and process recent splits from MongoDB with caching"""
    try:
        reverse_collection = get_collection(REVERSE_SPLITS_COLLECTION)
    except Exception:
        return []
    
    today = datetime.now().date()
    three_days_ago = today - timedelta(days=3)
    
    splits = list(reverse_collection.find({}).sort("Date", -1))
    
    recent_splits = []
    for split in splits:
        split_date_str = split.get("Date", "")
        split_date = parse_date(split_date_str)
        
        if not split_date:
            continue
        
        try:
            split_date_obj = datetime.strptime(split_date, "%Y-%m-%d").date()
        except:
            continue
            
        if split_date_obj >= three_days_ago:
            reverse_splits_id = str(split.get("_id"))
            
            has_edgar = has_edgar_data(reverse_splits_id)
            
            rounding = False
            if has_edgar:
                rounding = check_rounding_flag(reverse_splits_id)
            
            rounding_filings = []
            if rounding:
                rounding_filings = get_rounding_filings(reverse_splits_id)
            
            # Clean non-serializable ObjectId
            split_doc_clean = {k: v for k, v in split.items() if k != "_id"}
            
            recent_splits.append({
                "Date": split_date_str,
                "Symbol": split.get("Symbol", ""),
                "Company Name": split.get("Company Name", ""),
                "Split Ratio": split.get("Split Ratio", ""),
                "Rounding": "Yes" if rounding else "",
                "Has EDGAR": has_edgar,
                "reverse_splits_id": reverse_splits_id,
                "split_date_obj_str": split_date_obj.strftime("%Y-%m-%d"),
                "split_doc": split_doc_clean,
                "rounding_filings": rounding_filings
            })
    return recent_splits

@st.cache_data(ttl=600)  # Cache for 10 minutes
def fetch_early_splits():
    """Fetch all early EDGAR split warnings from MongoDB with caching"""
    try:
        early_collection = get_collection(EARLY_WARNINGS_COLLECTION)
    except Exception:
        return []
        
    early_splits = list(early_collection.find({}).sort("filing_date", -1))
    
    cleaned_splits = []
    for split in early_splits:
        clean = {k: v for k, v in split.items() if k != "_id"}
        clean["_id"] = str(split["_id"])
        cleaned_splits.append(clean)
        
    return cleaned_splits

def process_splits_without_edgar(splits_to_process, cik_mappings):
    """Process splits without EDGAR data"""
    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, split_info in enumerate(splits_to_process):
        symbol = split_info["Symbol"]
        status_text.text(f"Processing {symbol} ({i+1}/{len(splits_to_process)})...")
        
        try:
            result = process_reverse_split_with_edgar(
                split_info["split_doc"], 
                cik_mappings["ticker"], 
                cik_mappings["name"],
                skip_existing=False
            )
            results.append({"symbol": symbol, "status": "success", "result": result})
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})
        
        progress_bar.progress((i + 1) / len(splits_to_process))
    
    progress_bar.empty()
    status_text.empty()
    return results

def run_dashboard():
    st.set_page_config(
        page_title="Reverse Splits Dashboard",
        page_icon=None,
        layout="wide"
    )
    
    st.title("Recent Reverse Splits Dashboard")
    
    # Add refresh button to clear cache
    col1, col2 = st.columns([1, 10])
    with col1:
        if st.button("Refresh", help="Clear cache and reload data from MongoDB"):
            st.cache_data.clear()
            st.rerun()
    
    st.markdown("---")
    
    # Fetch recent splits using cached function
    try:
        raw_recent_splits = fetch_recent_splits()
    except Exception as e:
        st.error(f"Error fetching splits from database: {e}")
        return
    
    # Reconstruct date objects in memory for compatibility
    recent_splits = []
    for s in raw_recent_splits:
        try:
            split_date_obj = datetime.strptime(s["split_date_obj_str"], "%Y-%m-%d").date()
            s_copy = dict(s)
            s_copy["split_date_obj"] = split_date_obj
            recent_splits.append(s_copy)
        except Exception:
            continue

    # Get date range - Show splits from 3 days ago onwards (including all future dates)
    today = datetime.now().date()
    three_days_ago = today - timedelta(days=3)
    
    # Create tabs for different views
    tab1, tab2, tab3 = st.tabs(["Confirmed Splits", "Early Warnings", "Action Center"])
    
    with tab1:
        if not recent_splits:
            st.info("No recent reverse splits found (from 3 days ago to future).")
        else:
            # Sort by date (most recent first)
            recent_splits.sort(key=lambda x: x["split_date_obj"], reverse=True)
            
            # Identify splits that need EDGAR processing
            splits_to_process = [s for s in recent_splits if not s["Has EDGAR"]]
            
            # Process splits without EDGAR data
            if splits_to_process:
                st.warning(f"Found {len(splits_to_process)} split(s) without EDGAR data.")
                
                if st.button(f"Process EDGAR Data for {len(splits_to_process)} Split(s)", type="primary"):
                    cik_mappings = get_cik_mappings()
                    results = process_splits_without_edgar(splits_to_process, cik_mappings)
                    
                    # Show results
                    success_count = sum(1 for r in results if r["status"] == "success")
                    st.success(f"Processed {success_count}/{len(results)} splits successfully!")
                    
                    # Refresh to show updated data
                    st.rerun()
                
                # Show which splits need processing
                with st.expander(f"View {len(splits_to_process)} splits needing EDGAR processing"):
                    missing_df = pd.DataFrame([
                        {
                            "Symbol": s["Symbol"],
                            "Date": s["Date"],
                            "Company Name": s["Company Name"]
                        }
                        for s in splits_to_process
                    ])
                    st.dataframe(missing_df, use_container_width=True, hide_index=True)
            
            # Prepare DataFrame for display
            display_data = []
            for split_info in recent_splits:
                # Determine if should highlight (within last 3 days)
                is_recent = split_info["split_date_obj"] >= three_days_ago and split_info["split_date_obj"] <= today
                
                display_data.append({
                    "Date": split_info["Date"],
                    "Symbol": split_info["Symbol"],
                    "Company Name": split_info["Company Name"],
                    "Split Ratio": split_info["Split Ratio"],
                    "Rounding": split_info["Rounding"],
                    "_highlight": is_recent
                })
            
            df = pd.DataFrame(display_data)
            
            # Remove highlight column before display
            highlight_mask = df.pop("_highlight")
            
            # Display table with highlighting
            st.subheader(f"Recent Reverse Splits ({len(recent_splits)} total)")
            st.caption(f"Showing splits from {three_days_ago.strftime('%Y-%m-%d')} onwards. Highlighted rows are within the last 3 days.")
            
            # Style the dataframe
            def highlight_recent(row):
                if row.name < len(highlight_mask) and highlight_mask.iloc[row.name]:
                    return ['background-color: #e3f2fd; color: #000000; font-weight: 600'] * len(row)
                return ['background-color: #ffffff; color: #212121'] * len(row)
            
            styled_df = df.style.apply(highlight_recent, axis=1).set_properties(**{
                'text-align': 'left',
                'padding': '8px'
            })
            
            st.dataframe(styled_df, use_container_width=True, hide_index=True, height=400)
            
            # Show EDGAR filings for rows with rounding
            rounding_splits = [s for s in recent_splits if s["Rounding"] == "Yes" and s["rounding_filings"]]
            if rounding_splits:
                st.markdown("---")
                st.subheader("EDGAR Filings with Rounding Compliance")
                st.caption("Click to expand and view the actual EDGAR filings that contain rounding language")
                
                for split_info in rounding_splits:
                    symbol = split_info["Symbol"]
                    company_name = split_info["Company Name"]
                    split_date = split_info["Date"]
                    filings = split_info["rounding_filings"]
                    
                    with st.expander(f"{symbol} - {company_name} ({split_date}) - {len(filings)} filing(s)"):
                        for i, filing in enumerate(filings, 1):
                            st.markdown(f"**Filing {i}: {filing['form']}**")
                            
                            col1, col2 = st.columns([2, 1])
                            with col1:
                                st.write(f"**Filing Date:** {filing['filing_date']}")
                                st.write(f"**Accession:** {filing['accession']}")
                            with col2:
                                if filing['document_url']:
                                    st.markdown(f"[View Filing]({filing['document_url']})")
                            
                            # Show rounding text snippet
                            if filing.get('rounding_text'):
                                st.markdown("**Relevant Text:**")
                                st.code(filing['rounding_text'], language=None)
                            
                            if i < len(filings):
                                st.markdown("---")
            
            # Summary stats
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Recent Splits", len(recent_splits))
            with col2:
                recent_count = sum(1 for s in recent_splits if s["split_date_obj"] >= three_days_ago and s["split_date_obj"] <= today)
                st.metric("Last 3 Days", recent_count)
            with col3:
                rounding_count = sum(1 for s in recent_splits if s["Rounding"] == "Yes")
                st.metric("With Rounding", rounding_count)
            
    with tab2:
        st.header("Early EDGAR (Announced Splits)")
        st.markdown("""
        Real-time feed of **confirmed** reverse split announcements detected in 8-K/6-K filings.
        - **Rounding**: Indicates if fractional shares will be rounded up (Major Opportunity).
        - **Confidence**: AI analysis confidence level.
        """)
        
        try:
            # Fetch early splits using cached function
            early_splits = fetch_early_splits()
            
            if not early_splits:
                st.info("No announced splits found yet. Run the `scan_early_edgar.py` script to populate.")
            else:
                # 1. Clean, Premium Filtering Layout
                st.markdown("### Filter Announcements")
                
                rounding_options = ["YES", "NO", "?"]
                confidence_options = ["HIGH", "MEDIUM", "LOW", "N/A"]
                
                # Determine dates in dataset for defaults
                filing_dates = []
                effective_dates = []
                for p in early_splits:
                    fd = p.get("filing_date")
                    if fd:
                        try:
                            filing_dates.append(datetime.strptime(fd[:10], "%Y-%m-%d").date())
                        except:
                            pass
                    ed = p.get("effective_date")
                    if ed and ed != "Pending":
                        try:
                            effective_dates.append(datetime.strptime(ed[:10], "%Y-%m-%d").date())
                        except:
                            pass
                
                min_filing = min(filing_dates) if filing_dates else datetime.now().date() - timedelta(days=365)
                max_filing = max(filing_dates) if filing_dates else datetime.now().date() + timedelta(days=30)
                
                min_effective = min(effective_dates) if effective_dates else datetime.now().date() - timedelta(days=30)
                max_effective = max(effective_dates) if effective_dates else datetime.now().date() + timedelta(days=365)
                
                col1, col2, col3 = st.columns([3, 2.5, 2.5])
                
                with col1:
                    search_query = st.text_input(
                        "Search Ticker or Company", 
                        value="", 
                        placeholder="Search...",
                        key="early_search"
                    )
                
                with col2:
                    selected_rounding = st.multiselect(
                        "Rounding", 
                        options=rounding_options, 
                        default=[],
                        placeholder="All Rounding Options",
                        key="early_round"
                    )
                
                with col3:
                    # Get unique confidences present, or fallback to default options
                    unique_confs = sorted(list(set([str(p.get("confidence", "N/A")).upper() for p in early_splits if p.get("confidence")])))
                    if not unique_confs:
                        unique_confs = confidence_options
                    
                    selected_confidence = st.multiselect(
                        "AI Confidence", 
                        options=unique_confs, 
                        default=[],
                        placeholder="All Confidence Levels",
                        key="early_conf"
                    )
                
                col_date1, col_date2, col_reset = st.columns([3, 3, 1])
                
                with col_date1:
                    filing_range = st.date_input(
                        "Filing Date Range",
                        value=(min_filing, max_filing),
                        key="early_filing_range"
                    )
                
                with col_date2:
                    effective_range = st.date_input(
                        "Effective Date Range",
                        value=(min_effective, max_effective),
                        key="early_effective_range"
                    )
                
                with col_reset:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("Reset Filters", use_container_width=True):
                        st.session_state.early_search = ""
                        st.session_state.early_round = []
                        st.session_state.early_conf = []
                        st.session_state.early_filing_range = (min_filing, max_filing)
                        st.session_state.early_effective_range = (min_effective, max_effective)
                        st.rerun()

                # 2. Process Raw Data
                display_data = []
                for p in early_splits:
                    rounding = "?"
                    if p.get("rounding_up") is True:
                        rounding = "YES"
                    elif p.get("rounding_up") is False:
                        rounding = "NO"
                        
                    display_data.append({
                        "Filing Date": p.get("filing_date"),
                        "Ticker": p.get("ticker", "UNKNOWN"),
                        "Company": p.get("company_name"),
                        "Effective Date": p.get("effective_date", "Pending"),
                        "Ratio": p.get("ratio", "?"),
                        "Rounding": rounding,
                        "Summary": p.get("summary", ""),
                        "Confidence": str(p.get("confidence", "N/A")).upper(),
                        "Link": p.get("filing_url")
                    })
                
                # 3. Apply Filters in Python
                filtered_data = []
                for row in display_data:
                    # Search Filter
                    query = search_query.strip().lower()
                    if query:
                        ticker_match = query in row["Ticker"].lower()
                        company_match = query in (row["Company"] or "").lower()
                        if not (ticker_match or company_match):
                            continue
                    
                    # Rounding Filter (show all if empty/no selection)
                    if selected_rounding and row["Rounding"] not in selected_rounding:
                        continue
                    
                    # Confidence Filter (show all if empty/no selection)
                    if selected_confidence and row["Confidence"] not in selected_confidence:
                        continue

                    # Filing Date Range Filter
                    if filing_range and len(filing_range) == 2:
                        start_date, end_date = filing_range
                        row_date_str = row["Filing Date"]
                        if row_date_str:
                            try:
                                if isinstance(row_date_str, str):
                                    row_date = datetime.strptime(row_date_str[:10], "%Y-%m-%d").date()
                                else:
                                    row_date = row_date_str.date() if hasattr(row_date_str, 'date') else row_date_str
                                if not (start_date <= row_date <= end_date):
                                    continue
                            except:
                                pass

                    # Effective Date Range Filter
                    if effective_range and len(effective_range) == 2:
                        start_date, end_date = effective_range
                        row_date_str = row["Effective Date"]
                        if row_date_str and row_date_str != "Pending":
                            try:
                                if isinstance(row_date_str, str):
                                    row_date = datetime.strptime(row_date_str[:10], "%Y-%m-%d").date()
                                else:
                                    row_date = row_date_str.date() if hasattr(row_date_str, 'date') else row_date_str
                                if not (start_date <= row_date <= end_date):
                                    continue
                            except:
                                continue
                        else:
                            continue
                    
                    filtered_data.append(row)
                
                st.success(f"Showing {len(filtered_data)} of {len(early_splits)} confirmed announcements.")
                
                if not filtered_data:
                    st.info("No announcements match the selected filter criteria.")
                else:
                    # Put Rounding == YES at the top (stable sort preserves filing_date desc order within groups)
                    filtered_data.sort(key=lambda x: 0 if x["Rounding"] == "YES" else 1)
                    
                    e_df = pd.DataFrame(filtered_data)
                    
                    if "Filing Date" in e_df.columns:
                        try:
                            e_df["Filing Date"] = pd.to_datetime(e_df["Filing Date"])
                        except:
                            pass
                    
                    st.dataframe(
                        e_df,
                        column_config={
                            "Link": st.column_config.LinkColumn("Filing URL", display_text="View Filing"),
                            "Filing Date": st.column_config.DateColumn("Filing Date", format="YYYY-MM-DD"),
                            "Summary": st.column_config.TextColumn("AI Summary", width="large", help="Full summary available on hover"),
                            "Rounding": st.column_config.TextColumn("Rounding Up?", help="Does the filing explicitly state fractional shares are rounded up?"),
                        },
                        hide_index=True,
                        use_container_width=True,
                        height=400
                    )
                
        except Exception as e:
            st.error(f"Error fetching Early EDGAR data: {e}")

    with tab3:
        st.header("Action Center: Upcoming Trades")
        st.markdown("""
        Consolidated view of all **confirmed upcoming splits** combined with their **SEC AI Confidence** scores.
        Only trade the green **APPROVED** setups.
        """)
        
        st.warning("""
        **CRITICAL SAFETY FILTERS BEFORE ENTERING A TRADE:**
        1. **1-Day Notice Rule:** You must have at least 1 full day of holding time. If you missed the announcement and the split is happening today/tomorrow, DO NOT ENTER.
        2. **Gap-Up Rule:** If the stock is gapping up >30% in the pre-market on the morning you plan to enter, CANCEL THE TRADE.
        """)
        
        # 1. Get upcoming splits from recent_splits
        today = datetime.now().date()
        upcoming_action = [s for s in recent_splits if s["split_date_obj"] >= today]
        
        # Sort chronologically
        upcoming_action.sort(key=lambda x: x["split_date_obj"])
        
        # 2. Fetch EDGAR data to cross-reference AI Scores
        try:
            edgar_coll = get_collection(EDGAR_COLLECTION)
            early_coll = get_collection(EARLY_WARNINGS_COLLECTION)
        except Exception:
            edgar_coll = None
            early_coll = None
            
        action_data = []
        for s in upcoming_action:
            tier = "MISSING"
            summary = ""
            announce_date = "N/A"
            
            # Check EARLY_WARNINGS_COLLECTION first (recent data)
            if early_coll is not None:
                # Some early_warnings tickers might end in 'W' or 'Z' but let's do a direct match first
                early_docs = list(early_coll.find({"ticker": {"$regex": f"^{s['Symbol']}", "$options": "i"}}))
                if early_docs:
                    for doc in early_docs:
                        doc_tier = str(doc.get("confidence", "")).upper()
                        if doc_tier == "HIGH":
                            tier = "HIGH"
                            summary = doc.get("summary", "")
                            announce_date = doc.get("filing_date", "N/A")
                            if isinstance(announce_date, str) and len(announce_date) > 10:
                                announce_date = announce_date[:10]
                            # Fallback to AI rounding if missing
                            if not s["Rounding"]:
                                if doc.get("rounding_up") is True: s["Rounding"] = "YES"
                                elif doc.get("rounding_up") is False: s["Rounding"] = "NO"
                            break
                        elif doc_tier == "MEDIUM" and tier != "HIGH":
                            tier = "MEDIUM"
                            summary = doc.get("summary", "")
                            announce_date = doc.get("filing_date", "N/A")
                            if isinstance(announce_date, str) and len(announce_date) > 10:
                                announce_date = announce_date[:10]
            
            # Fallback to historical EDGAR_COLLECTION
            if tier == "MISSING" and s["Has EDGAR"] and edgar_coll is not None:
                edgar_docs = list(edgar_coll.find({"reverse_splits_id": s["reverse_splits_id"]}))
                if edgar_docs:
                    # Find highest tier
                    for doc in edgar_docs:
                        doc_tier = doc.get("tier", "C")
                        if doc_tier == "A":
                            tier = "HIGH"
                            summary = doc.get("text_matches", {}).get("summary", "")
                            announce_date = doc.get("filing_date", "N/A")
                            if isinstance(announce_date, str) and len(announce_date) > 10:
                                announce_date = announce_date[:10]
                            break
                        elif doc_tier == "B" and tier != "HIGH":
                            tier = "MEDIUM"
                            summary = doc.get("text_matches", {}).get("summary", "")
                            announce_date = doc.get("filing_date", "N/A")
                            if isinstance(announce_date, str) and len(announce_date) > 10:
                                announce_date = announce_date[:10]
                            
            # Determine Action Badge
            if tier == "HIGH":
                badge = "🟢 APPROVED"
            elif tier == "MEDIUM":
                badge = "🟡 MARGINAL"
            else:
                badge = "🔴 SKIP"
                
            action_data.append({
                "Execution Date": s["split_date_obj_str"],
                "Announce Date": announce_date,
                "Ticker": s["Symbol"],
                "Ratio": s["Split Ratio"],
                "Rounding": s["Rounding"],
                "AI Score": tier,
                "Action": badge,
                "Summary": summary
            })
            
        if not action_data:
            st.info("No upcoming splits on the radar right now.")
        else:
            action_df = pd.DataFrame(action_data)
            
            # Put APPROVED at the top, then sort by date
            action_df["_sort_badge"] = action_df["Action"].apply(lambda x: 0 if "APPROVED" in x else (1 if "MARGINAL" in x else 2))
            action_df = action_df.sort_values(by=["_sort_badge", "Execution Date"])
            action_df = action_df.drop(columns=["_sort_badge"])
            
            st.dataframe(
                action_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Summary": st.column_config.TextColumn("AI Summary", width="large")
                },
                height=350
            )
            
        st.markdown("---")
        st.subheader("Position Size Calculator")
        st.markdown("Calculate your exact short position based on our mathematical **40% Stop-Loss** strategy rules.")
        
        col1, col2 = st.columns(2)
        with col1:
            account_size = st.number_input("Total Trading Account Size ($)", value=10000, step=1000)
            risk_pct = st.number_input("Max Risk per Trade (%)", value=2.0, step=0.5, help="Percentage of your total account you are willing to lose on a single trade if stopped out.")
            
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            max_loss = account_size * (risk_pct / 100.0)
            position_size = max_loss / 0.40
            
            st.info(f"**Max Acceptable Loss:** ${max_loss:,.2f}")
            st.success(f"**Recommended Short Position Size:** ${position_size:,.2f}")
            st.caption("*If you short this dollar amount and the stock violently pumps 40% against you hitting your hard stop, you will only lose your exact Max Acceptable Loss.*")

    # Refresh button
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Refresh Data"):
            st.rerun()
    with col2:
        if st.button("Clear Cache"):
            st.cache_data.clear()
            st.success("Cache cleared! Refresh the page.")
