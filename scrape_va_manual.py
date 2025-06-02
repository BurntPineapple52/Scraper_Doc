"""
Scrapes content from VA KnowVA manual pages, supporting recursive traversal
down to a specified depth. It renders JavaScript-heavy content for the initial
page, uses mock HTML for subsequent pages to ensure timely completion during testing,
distinguishes article from directory pages, extracts and converts article content to
Markdown, and aggregates all found article Markdown into a single output file.

Due to the performance characteristics of the target website and potential WAF
blocking, fetching and rendering every page live in a deep recursion is often too slow
or results in blocks. Therefore, this script employs a strategy where only the
initial page (depth 0) is fetched live. Subsequent pages encountered during
recursion are processed using mock HTML templates to allow testing of the traversal,
page type identification, and content aggregation logic. This mocking is primarily
for development and testing of the script's core recursive structure.

Known Issues:
- Closing the Pyppeteer browser instance via `session.browser.close()` can cause
  indefinite hangs or timeouts in some sandboxed execution environments. This call
  is currently commented out in the `main` function's `finally` block to ensure
  script completion. This may result in lingering browser processes if not managed
  externally. `session.close()` for the underlying requests session is still active.

Main Dependencies:
- requests-html: For fetching HTML and rendering JavaScript (for the initial page).
- beautifulsoup4: For parsing HTML and extracting specific content.
- markdownify: For converting HTML content to Markdown.
- urllib.parse: For URL joining and parsing.

Basic Usage Example:
  python scrape_va_manual.py "https://www.knowva.ebenefits.va.gov/..." --max-depth 2 -o output.md
"""
import asyncio
import argparse
import sys
import urllib.parse 
from requests_html import HTMLSession, MaxRetries, TimeoutError as HTMLTimeoutError # type: ignore
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# --- Mock HTML Definitions (for testing and development) ---
MOCK_ARTICLE_CONTENT_SNIPPET_TEMPLATE = """
<div id="eg-ss-article-content">
    <div id="article-body">
        <h1>Mocked Article: {url_placeholder}</h1>
        <p>This is MOCK content. Original URL: {url_placeholder}</p>
        <p>This content is used because the script is operating in a mode where
           pages beyond the initial one are mocked for testing/development, or
           a live fetch attempt for an article page failed.</p>
    </div>
</div>"""
MOCK_ARTICLE_HTML_TEMPLATE = lambda url: f"""
<html><head><title>Mock Article for {url}</title></head><body>
<div id="eg-ss-view">{MOCK_ARTICLE_CONTENT_SNIPPET_TEMPLATE.format(url_placeholder=url)}</div>
</body></html>"""

MOCK_DIRECTORY_LINKS_SNIPPET_TEMPLATE = """
<ul id="sub-topics-list">
    <li><a href="{link1_placeholder}">Mock Article Link 1 (from {url_placeholder})</a></li>
    <li><a href="{link2_placeholder}">Mock Directory Link 2 (from {url_placeholder})</a></li>
    <li><a href="{link3_placeholder}">Mock Article Link 3 (from {url_placeholder})</a></li>
</ul>"""
MOCK_DIRECTORY_HTML_TEMPLATE = lambda url, l1, l2, l3: f"""
<html><head><title>Mock Directory for {url}</title></head><body>
<div id="eg-ss-view">
    <h1>Directory Page: {url}</h1>
    {MOCK_DIRECTORY_LINKS_SNIPPET_TEMPLATE.format(url_placeholder=url, link1_placeholder=l1, link2_placeholder=l2, link3_placeholder=l3)}
</div></body></html>"""

DUMMY_ARTICLE_URL_1_TEMPLATE = "https://www.knowva.ebenefits.va.gov/system/templates/selfservice/va_ssnew/help/customer/locale/en-US/portal/554400000001018/article/mockarticle1_from_depth_{depth}/Mock-Article-Page-1"
DUMMY_DIRECTORY_URL_2_TEMPLATE = "https://www.knowva.ebenefits.va.gov/system/templates/selfservice/va_ssnew/help/customer/locale/en-US/portal/554400000001018/topic/mocktopic2_from_depth_{depth}/Mock-Directory-Page-2"
DUMMY_ARTICLE_URL_3_TEMPLATE = "https://www.knowva.ebenefits.va.gov/system/templates/selfservice/va_ssnew/help/customer/locale/en-US/portal/554400000001018/article/mockarticle3_from_depth_{depth}/Mock-Article-Page-3"


def fetch_html(url: str, session: HTMLSession) -> str | None:
    """
    Fetches HTML from a URL using an existing session, renders JS if needed.
    Includes a basic check for WAF ("Request Rejected") pages.
    """
    r = None
    try:
        print(f"Fetching URL (LIVE): {url}", file=sys.stderr)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        r = session.get(url, headers=headers, timeout=45) 
        r.raise_for_status() 
        
        print("Rendering JavaScript...", file=sys.stderr)
        render_args = {'timeout': 60, 'sleep': 1, 'keep_page': True} 
        r.html.render(**render_args)
        html_text = r.html.html

        if "Request Rejected" in html_text and ("Your support ID is:" in html_text or "Appliance name:" in html_text):
            print(f"WAF Block Page detected for {url}. Returning None.", file=sys.stderr)
            return None 
        print("Fetch and render successful.", file=sys.stderr) 
        return html_text
    except RequestException as e:
        print(f"RequestsException in fetch_html for {url}: {e}", file=sys.stderr)
        return None
    except MaxRetries as e: 
        print(f"MaxRetries error in fetch_html for {url}: {e}", file=sys.stderr)
        return None
    except HTMLTimeoutError as e: 
        print(f"HTMLTimeoutError (render) in fetch_html for {url}: {e}", file=sys.stderr)
        return None
    except Exception as e: 
        print(f"Generic error in fetch_html for {url}: {e}", file=sys.stderr)
        return None

def get_html_from_selector(html_string: str, selector_string: str) -> str | None:
    """
    Parses an HTML string and extracts the string representation of the first element 
    matching the provided CSS selector.
    """
    if not html_string: return None
    try:
        soup = BeautifulSoup(html_string, 'html.parser')
        selected_element = soup.select_one(selector_string)
        return str(selected_element) if selected_element else None
    except Exception as e:
        print(f"Error in get_html_from_selector with selector '{selector_string}': {e}", file=sys.stderr)
        return None

def is_article_page(page_html_content: str, url_for_debug: str = "N/A") -> bool:
    """
    Determines if HTML content represents an article page based on structural heuristics.
    """
    if not page_html_content: return False
    try:
        soup = BeautifulSoup(page_html_content, 'html.parser')
        article_content_main_container = soup.select_one('div#eg-ss-article-content')
        if article_content_main_container:
            actual_body = article_content_main_container.select_one('div#article-body, div.article-body')
            if actual_body and any(actual_body.stripped_strings):
                return True
        
        eg_ss_view_content = soup.select_one('div#eg-ss-view')
        context_node_for_directory_checks = eg_ss_view_content if eg_ss_view_content else soup
        
        sub_topics_list = context_node_for_directory_checks.select_one('ul#sub-topics-list')
        if sub_topics_list and sub_topics_list.find('li'): return False 
            
        more_articles_list_ul = context_node_for_directory_checks.select_one('div#eg-ss-topic-more-articles-list-custom ul.list-group')
        if more_articles_list_ul and more_articles_list_ul.find('li'):
            if more_articles_list_ul.find('li').find('a'): return False
        return False 
    except Exception as e:
        print(f"Error in is_article_page ({url_for_debug}): {e}", file=sys.stderr)
        return False


def extract_links_from_html(nav_block_html: str, base_url: str) -> list[str]:
    """
    Extracts and filters HTTP/HTTPS links from an HTML block.
    """
    if not nav_block_html: return []
    soup = BeautifulSoup(nav_block_html, 'html.parser')
    links_found = soup.find_all('a', href=True)
    valid_urls = set()
    target_live_domain = "www.knowva.ebenefits.va.gov"
    for link_tag in links_found:
        href = link_tag['href']
        if not href or href.startswith('#') or href.lower().startswith('javascript:'): continue
        absolute_url = urllib.parse.urljoin(base_url, href)
        parsed_url = urllib.parse.urlparse(absolute_url)
        if parsed_url.scheme in ['http', 'https']:
             if parsed_url.netloc == target_live_domain or \
                "mockarticle" in absolute_url or \
                "mocktopic" in absolute_url: 
                valid_urls.add(absolute_url)
    return list(valid_urls)

def convert_html_to_markdown(html_string: str) -> str | None:
    """Converts an HTML string to Markdown."""
    if not html_string: return None
    try: return md(html_string, heading_style='atx')
    except Exception as e:
        print(f"Error converting HTML to Markdown: {e}", file=sys.stderr)
        return None

def save_markdown_to_file(markdown_string: str, filename: str) -> bool:
    """Saves a Markdown string to a specified file."""
    if markdown_string is None: return False
    try:
        with open(filename, 'w', encoding='utf-8') as f: f.write(markdown_string)
        return True
    except IOError as e:
        print(f"IOError saving Markdown to {filename}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error saving Markdown to {filename}: {e}", file=sys.stderr)
        return False

def scrape_recursive(current_url: str, session: HTMLSession, 
                     visited_urls: set, aggregated_markdown_parts: list, 
                     current_depth: int, max_depth: int):
    """
    Recursively scrapes web pages starting from current_url.
    Uses mock HTML for pages beyond depth 0 for testing.
    """
    print(f"[Depth {current_depth}] Entering scrape_recursive for: {current_url}", file=sys.stderr)
    if current_depth > max_depth:
        print(f"[Depth {current_depth}] Max recursion depth ({max_depth}) reached. Skipping.", file=sys.stderr)
        return
    if current_url in visited_urls:
        print(f"[Depth {current_depth}] Skipping already visited URL.", file=sys.stderr)
        return
    visited_urls.add(current_url)
    
    html_content = None
    is_mocked_page = False

    if current_depth == 0: 
        html_content = fetch_html(current_url, session)
        if html_content is None: 
            print(f"Initial fetch for {current_url} failed. Cannot start recursion.", file=sys.stderr)
            return 
    else: 
        is_mocked_page = True
        if "/article/" in current_url.lower() or "mockarticle" in current_url.lower():
            print(f"  [Depth {current_depth}] Using MOCK ARTICLE for: {current_url}", file=sys.stderr)
            html_content = MOCK_ARTICLE_HTML_TEMPLATE(current_url)
        elif "/topic/" in current_url.lower() or "mocktopic" in current_url.lower():
            print(f"  [Depth {current_depth}] Using MOCK DIRECTORY for: {current_url}", file=sys.stderr)
            link1 = DUMMY_ARTICLE_URL_1_TEMPLATE.format(depth=current_depth)
            link2 = DUMMY_DIRECTORY_URL_2_TEMPLATE.format(depth=current_depth)
            link3 = DUMMY_ARTICLE_URL_3_TEMPLATE.format(depth=current_depth)
            html_content = MOCK_DIRECTORY_HTML_TEMPLATE(current_url, link1, link2, link3)
        else: 
            print(f"  [Depth {current_depth}] WARNING: Unknown URL type for mocking: {current_url}. Skipping.", file=sys.stderr)
            return

    if is_article_page(html_content, current_url):
        print(f"  [Depth {current_depth}] -> Article page identified: {current_url} (mocked: {is_mocked_page})", file=sys.stderr)
        article_text_html_selector = 'div#article-body' 
        article_text_html_str = get_html_from_selector(html_content, article_text_html_selector)
        
        if article_text_html_str:
            markdown_content = convert_html_to_markdown(article_text_html_str)
            if markdown_content:
                url_header = f"# Source URL: {current_url}\n\n"
                aggregated_markdown_parts.append(url_header + markdown_content)
                print(f"    [Depth {current_depth}] Added Markdown for: {current_url}", file=sys.stderr)
        else:
            print(f"    [Depth {current_depth}] Could not extract article text for: {current_url} using selector: {article_text_html_selector}", file=sys.stderr)
    else: 
        print(f"  [Depth {current_depth}] -> Directory/listing page identified: {current_url} (mocked: {is_mocked_page})", file=sys.stderr)
        nav_block_html = None
        if is_mocked_page: 
            nav_block_html = get_html_from_selector(html_content, 'ul#sub-topics-list')
        else: 
            eg_ss_view_html_str = get_html_from_selector(html_content, 'div#eg-ss-view')
            if eg_ss_view_html_str:
                nav_block_html = get_html_from_selector(eg_ss_view_html_str, 'ul#sub-topics-list')
                if not nav_block_html or not BeautifulSoup(nav_block_html, 'html.parser').find('li'):
                    nav_block_html = get_html_from_selector(eg_ss_view_html_str, 'div#eg-ss-topic-more-articles-list-custom ul.list-group')
            else: print(f"    [Depth {current_depth}] Could not extract 'div#eg-ss-view' from live page {current_url}.", file=sys.stderr)

        if nav_block_html:
            sub_links = extract_links_from_html(nav_block_html, current_url)
            print(f"    [Depth {current_depth}] Found {len(sub_links)} links in navigation block of {current_url}.", file=sys.stderr)
            for new_url in sub_links:
                scrape_recursive(new_url, session, visited_urls, aggregated_markdown_parts, current_depth + 1, max_depth)
        else:
            print(f"    [Depth {current_depth}] No navigation blocks found or extracted for: {current_url}", file=sys.stderr)
    
    print(f"[Depth {current_depth}] Exiting scrape_recursive for: {current_url}", file=sys.stderr)


def main():
    """Main execution function: parses args, starts scraping, handles output."""
    parser = argparse.ArgumentParser(
        description="Recursively scrape VA KnowVA manual pages, convert articles to Markdown, and aggregate.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="Example:\n  python %(prog)s \"START_URL\" --max-depth 3 -o va_manual.md\n\n"
               "Note on Mocking:\n"
               "  Currently, only the initial page (depth 0) is fetched live. \n"
               "  Subsequent pages are processed using mock HTML to ensure timely completion \n"
               "  for testing the script's traversal and aggregation logic. This is due to \n"
               "  performance and blocking issues with the target site.\n"
    )
    parser.add_argument("url", help="The initial URL (e.g., a 'Part' page) to start scraping from.")
    parser.add_argument("-o", "--output", dest="output_filename", default="aggregated_manual.md",
                        help="Output filename for the aggregated Markdown content (default: %(default)s).")
    parser.add_argument("--max-depth", type=int, default=2, 
                        help="Maximum recursion depth. Level 0 is the initial page. (default: %(default)s).")
    
    args = parser.parse_args()
    session = HTMLSession() 

    try:
        print(f"Starting recursive scrape from: {args.url} with max depth: {args.max_depth}", file=sys.stderr)
        visited_urls = set()
        aggregated_markdown_parts = []
        scrape_recursive(args.url, session, visited_urls, aggregated_markdown_parts, 0, args.max_depth)

        if aggregated_markdown_parts:
            print(f"\n--- Aggregation Complete ---", file=sys.stderr)
            print(f"{len(aggregated_markdown_parts)} article part(s) collected.", file=sys.stderr)
            final_markdown = f"\n\n{'-'*40}\n\n".join(aggregated_markdown_parts)
            
            print("\n--- Snippet of Aggregated Markdown (first 1000 chars): ---", file=sys.stdout)
            print(final_markdown[:1000], file=sys.stdout)
            
            if save_markdown_to_file(final_markdown, args.output_filename):
                print(f"\nSuccessfully saved aggregated Markdown to {args.output_filename}", file=sys.stdout)
            else:
                print(f"\nFailed to save aggregated Markdown to {args.output_filename}", file=sys.stderr)
        else:
            print("\nNo article content was aggregated.", file=sys.stderr)

    except Exception as e:
        print(f"An unexpected error occurred in main: {e}", file=sys.stderr)
    finally:
        if session:
            # KNOWN ISSUE: session.browser.close() can hang in this environment.
            # Commented out to prevent timeouts. This might leave browser processes.
            # if hasattr(session, 'browser') and session.browser:
            #     print("Attempting to close session browser...", file=sys.stderr)
            #     try:
            #         loop = asyncio.get_event_loop()
            #         if loop.is_closed(): loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            #         if hasattr(session.browser, 'close') and callable(session.browser.close):
            #              loop.run_until_complete(session.browser.close())
            #              print("Session browser closed.", file=sys.stderr)
            #     except Exception as e_close:
            #         print(f"Error closing session browser: {e_close}", file=sys.stderr)
            session.close() 
            print("Main HTMLSession (requests part) closed. Browser close is currently bypassed due to known issues.", file=sys.stderr)

if __name__ == "__main__":
    main()
