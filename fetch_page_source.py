import asyncio
from requests_html import AsyncHTMLSession # Changed here

async def main():
    url = "https://www.knowva.ebenefits.va.gov/system/templates/selfservice/va_ssnew/help/customer/locale/en-US/portal/554400000001018/topic/554400000004049/M21-1-Adjudication-Procedures-Manual"
    session = AsyncHTMLSession() # Changed here
    r = None # Define r outside try block
    try:
        print(f"Fetching URL (LIVE): {url}", flush=True)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        # For AsyncHTMLSession, get() is an async method
        r = await session.get(url, headers=headers, timeout=45) 
        await r.html.arender(timeout=60, sleep=2, keep_page=False)
        html_text = r.html.html

        if "Request Rejected" in html_text and ("Your support ID is:" in html_text or "Appliance name:" in html_text):
            print(f"WAF Block Page detected for {url}. Printing what was received.", flush=True)
        
        print("\n--- HTML CONTENT START ---")
        print(html_text)
        print("--- HTML CONTENT END ---")

    except Exception as e:
        print(f"An error occurred: {e}", flush=True)
    finally:
        if r: # Ensure r exists before trying to close (though not strictly necessary for AsyncHTMLSession's main session)
             # AsyncHTMLSession itself is closed, browser management is handled by arender/aclose
            pass
        await session.close() # This closes the underlying session and browser if managed by AsyncHTMLSession

if __name__ == "__main__":
    asyncio.run(main())
