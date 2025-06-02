"""
Scrapes content from a VA KnowVA manual page, renders JavaScript-heavy content,
extracts the main article, converts it to Markdown, and saves it to a file.

This script is designed to handle pages that require JavaScript rendering
to display their full content.

Main Dependencies:
- requests-html: For fetching HTML and rendering JavaScript.
- beautifulsoup4: For parsing HTML and extracting specific content.
- markdownify: For converting HTML content to Markdown.

Basic Usage Example:
  python scrape_va_manual.py "https://example.com/va_manual_page" -o output.md

Command-line Arguments:
  url (required): The URL of the VA manual page to scrape.
  -o, --output (optional): The filename for the saved Markdown output.
                           Defaults to "output.md".
"""
import asyncio
import argparse
import sys
from requests_html import HTMLSession, MaxRetries, TimeoutError as HTMLTimeoutError
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
from markdownify import markdownify as md

def fetch_html(url: str) -> str | None:
    """
    Fetches HTML content from a given URL, attempts to render JavaScript,
    and returns the final HTML content as a string.

    Args:
        url (str): The URL to fetch HTML from.

    Returns:
        str | None: The rendered HTML content as a string if successful, 
                    or None if any error occurs during fetching or rendering.
    """
    session = HTMLSession()
    r = None  # Initialize r to ensure it's defined for the finally block
    try:
        print(f"Fetching URL: {url}", file=sys.stderr)
        r = session.get(url, timeout=30)  # Overall timeout for the GET request
        r.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        
        print("Rendering JavaScript (with a delay for content loading)...", file=sys.stderr)
        # Arguments for r.html.render():
        #   timeout: Max time to wait for rendering.
        #   sleep: Time in seconds to wait after initial load before capturing HTML,
        #          allowing async JavaScript content to populate.
        #   keep_page: Keep the browser page open in memory for potential further
        #              interactions (important for some sites, and for explicit closing).
        render_args = {'timeout': 60, 'sleep': 5, 'keep_page': True} 
        r.html.render(**render_args)
        
        print("JavaScript rendered successfully.", file=sys.stderr)
        return r.html.html  # Return the HTML content after JavaScript rendering
    except HTMLTimeoutError:  # Specific to requests-html render timeout
        print(f"Timeout occurred while rendering JavaScript for {url}", file=sys.stderr)
        return None
    except MaxRetries:  # Specific to requests-html if retries are configured and exceeded
        print(f"Max retries exceeded for {url}", file=sys.stderr)
        return None
    except RequestException as e:  # Covers network issues, HTTP errors, etc., from requests library
        print(f"Error during HTTP request for {url}: {e}", file=sys.stderr)
        return None
    except Exception as e:  # Catch-all for other unexpected errors (e.g., pyppeteer issues)
        print(f"An unexpected error occurred in fetch_html for {url}: {e}", file=sys.stderr)
        return None
    finally:
        # Ensure resources are cleaned up
        if r and r.html and hasattr(r.html, 'page') and r.html.page:
            try:
                # Get the current event loop
                loop = asyncio.get_event_loop()
                if loop.is_running(): 
                    # If loop is running (e.g., in Jupyter or other async contexts),
                    # schedule page close without blocking.
                    asyncio.ensure_future(r.html.page.close())
                else: 
                    # If no loop is running (typical for simple script execution),
                    # run page close until completion.
                    loop.run_until_complete(r.html.page.close())
                print("Pyppeteer page closed.", file=sys.stderr)
            except Exception as e:
                print(f"Error closing Pyppeteer page: {e}", file=sys.stderr)
        if session:
            session.close()
            print("HTMLSession closed.", file=sys.stderr)

def extract_content(html_string: str) -> str | None:
    """
    Parses an HTML string and extracts the main content container.

    The specific container (`div` with id `eg-ss-view`) was identified through
    manual inspection of the target website's HTML structure.

    Args:
        html_string (str): The HTML content (as a string) to parse.

    Returns:
        str | None: The HTML content of the main container as a string if found,
                    otherwise None.
    """
    if not html_string:
        print("HTML string for extraction is empty.", file=sys.stderr)
        return None
    try:
        soup = BeautifulSoup(html_string, 'html.parser')
        # The target div was found to contain the primary article content on KnowVA pages.
        content_container = soup.find('div', id='eg-ss-view')
        
        if content_container:
            if content_container.contents:  # Check if the div has any children/content
                return str(content_container) # Return the HTML of the div itself
            else:
                print("Main content container ('div#eg-ss-view') was found but is empty.", file=sys.stderr)
                return None
        else:
            print("Main content container ('div#eg-ss-view') not found in HTML.", file=sys.stderr)
            return None
    except Exception as e:  # Catch potential BeautifulSoup parsing errors
        print(f"Error parsing or extracting content: {e}", file=sys.stderr)
        return None

def convert_html_to_markdown(html_string: str) -> str | None:
    """
    Converts an HTML string to a Markdown formatted string.

    Args:
        html_string (str): The HTML content to convert.

    Returns:
        str | None: The converted Markdown string, or None if conversion fails.
    """
    if not html_string:
        print("HTML string for Markdown conversion is empty.", file=sys.stderr)
        return None
    try:
        # 'atx' heading_style means headings will be formatted as '# Heading 1', '## Heading 2', etc.
        markdown_text = md(html_string, heading_style='atx')
        return markdown_text
    except Exception as e:  # Catch potential errors from the markdownify library
        print(f"Error converting HTML to Markdown: {e}", file=sys.stderr)
        return None

def save_markdown_to_file(markdown_string: str, filename: str) -> bool:
    """
    Saves a Markdown string to a specified file.

    Args:
        markdown_string (str): The Markdown content to save.
        filename (str): The name of the file to save the Markdown to.

    Returns:
        bool: True if saving was successful, False otherwise.
    """
    if markdown_string is None: # Check if the input string is None
        print(f"Markdown string is None. Cannot save to {filename}.", file=sys.stderr)
        return False
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(markdown_string)
        return True
    except IOError as e:  # Specifically catch I/O errors like permission issues
        print(f"IOError saving Markdown to {filename}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # Catch any other unexpected errors during file save
        print(f"An unexpected error occurred while saving to {filename}: {e}", file=sys.stderr)
        return False

def main():
    """
    Main execution function for the scraper script.

    Parses command-line arguments, then orchestrates the fetching,
    extraction, conversion, and saving processes.
    Handles critical errors by exiting the script.
    """
    parser = argparse.ArgumentParser(
        description="Scrape web content from VA KnowVA, render JavaScript, convert to Markdown, and save.",
        epilog="Example: python scrape_va_manual.py \"<URL>\" -o output.md" 
    )
    parser.add_argument("url", help="The URL of the VA manual page to scrape.")
    parser.add_argument("-o", "--output", dest="output_filename", default="output.md",
                        help="Output filename for the Markdown content (default: output.md)")
    
    args = parser.parse_args()

    # Log initial parameters to stderr
    print(f"Processing URL: {args.url}", file=sys.stderr)
    print(f"Output will be saved to: {args.output_filename}", file=sys.stderr)

    # Step 1: Fetch HTML content (including JavaScript rendering)
    html_content = None
    try:
        html_content = fetch_html(args.url)
        if not html_content:
            print("Failed to fetch HTML content. Exiting.", file=sys.stderr)
            sys.exit(1) # Critical failure, exit script
        print("\nSuccessfully fetched and rendered HTML content.", file=sys.stderr)
    except Exception as e: 
        print(f"Critical error during HTML fetching: {e}. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Step 2: Extract the main content from the fetched HTML
    extracted_html_content = None
    try:
        extracted_html_content = extract_content(html_content)
        if not extracted_html_content:
            print("Failed to extract main content. Exiting.", file=sys.stderr)
            sys.exit(1) # Critical failure
        print("\nSuccessfully extracted main content HTML.", file=sys.stderr)
    except Exception as e:
        print(f"Critical error during content extraction: {e}. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Step 3: Convert the extracted HTML to Markdown
    markdown_content = None
    try:
        markdown_content = convert_html_to_markdown(extracted_html_content)
        if not markdown_content:
            print("Failed to convert HTML to Markdown. Exiting.", file=sys.stderr)
            sys.exit(1) # Critical failure
        print("\nSuccessfully converted HTML to Markdown.", file=sys.stderr)
    except Exception as e:
        print(f"Critical error during Markdown conversion: {e}. Exiting.", file=sys.stderr)
        sys.exit(1)
    
    # Step 4: Save the Markdown content to a file
    try:
        if save_markdown_to_file(markdown_content, args.output_filename):
            # Final success message to stdout for easy piping or logging if needed
            print(f"\nSuccessfully saved Markdown to {args.output_filename}", file=sys.stdout) 
        else:
            print(f"\nFailed to save Markdown to {args.output_filename}. Exiting.", file=sys.stderr)
            sys.exit(1) # Critical failure
    except Exception as e:
        print(f"Critical error during file saving: {e}. Exiting.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    # This check ensures that main() is called only when the script is executed directly,
    # not when it's imported as a module.
    main()
