import asyncio
import json
import re
from urllib.parse import quote

from apify import Actor
from playwright.async_api import async_playwright
import nokk


async def main() -> None:
    async with Actor:
        # Get input
        actor_input = await Actor.get_input() or {}
        query = actor_input.get('query', '')
        max_results = actor_input.get('max_results', 10)
        site = actor_input.get('site', '1337x')  # '1337x' or 'limetorrents'
        get_magnets = actor_input.get('get_magnets', False)

        Actor.log.info(f'Searching for: {query} on {site}')
        Actor.log.info(f'Max results: {max_results}')
        Actor.log.info(f'Extract magnets: {get_magnets}')

        results = []

        if site == '1337x':
            results = await scrape_1337x(query, max_results, get_magnets)
        elif site == 'limetorrents':
            results = await scrape_limetorrents(query, max_results, get_magnets)
        else:
            Actor.log.error(f'Unknown site: {site}')
            await Actor.exit()

        # Push results to dataset
        await Actor.push_data(results)

        Actor.log.info(f'Found {len(results)} results')


async def scrape_1337x(query: str, max_results: int, get_magnets: bool = False) -> list:
    """Scrape 1337x for torrent results"""
    results = []

    try:
        # Launch NOKK with auto_solve for Cloudflare
        server = nokk.launch(auto_solve=True)
        Actor.log.info('NOKK launched successfully')

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(server.ws_endpoint)
            page = await browser.new_page()

            # Build URL
            encoded_query = quote(query)
            url = f"https://1337x.to/search/{encoded_query}/1/"
            Actor.log.info(f'Visiting: {url}')

            await page.goto(url, timeout=60000)
            await page.wait_for_load_state('networkidle', timeout=30000)

            # Extract results
            tables = await page.locator('table.table-list').all()
            if not tables:
                Actor.log.warning('No results table found')
                await browser.close()
                return results

            table = tables[0]
            rows = await table.locator('tbody tr').all()

            Actor.log.info(f'Found {len(rows)} rows')

            for i, row in enumerate(rows):
                if len(results) >= max_results:
                    break

                try:
                    name_cell = row.locator('td.name').first
                    if await name_cell.count() == 0:
                        continue

                    name = await name_cell.text_content()
                    name = name.strip() if name else ''

                    links = await name_cell.locator('a').all()
                    if len(links) < 2:
                        continue

                    detail_href = await links[1].get_attribute('href')
                    detail_url = f"https://1337x.to{detail_href}" if detail_href else None

                    seeds_elem = row.locator('td.seeds').first
                    seeds = await seeds_elem.text_content()
                    seeds = seeds.strip() if seeds else '0'

                    leeches_elem = row.locator('td.leeches').first
                    leeches = await leeches_elem.text_content()
                    leeches = leeches.strip() if leeches else '0'

                    size_elem = row.locator('td.coll-4').first
                    size = await size_elem.text_content()
                    size = size.strip() if size else 'Unknown'
                    # Clean up size - sometimes it contains extra text
                    size_match = re.search(r'[\d.]+\s*(?:GB|MB|KB|TB)', size)
                    if size_match:
                        size = size_match.group(0)

                    magnet = None
                    if get_magnets and detail_url:
                        Actor.log.info(f'Extracting magnet for: {name}')
                        await page.goto(detail_url, timeout=60000)
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        magnet_links = await page.locator('a[href^="magnet:"]').all()
                        if magnet_links:
                            magnet = await magnet_links[0].get_attribute('href')
                        # Go back to search results
                        await page.goto(url, timeout=60000)
                        await page.wait_for_load_state('networkidle', timeout=30000)

                    if name and detail_url:
                        result = {
                            'name': name,
                            'detail_url': detail_url,
                            'seeds': seeds,
                            'leeches': leeches,
                            'size': size,
                            'site': '1337x'
                        }
                        if magnet:
                            result['magnet'] = magnet
                        results.append(result)
                        Actor.log.info(f'Added: {name} ({seeds} seeds)')

                except Exception as e:
                    Actor.log.error(f'Error processing row {i}: {e}')
                    continue

            await browser.close()

    except Exception as e:
        Actor.log.error(f'Error scraping 1337x: {e}')

    return results


async def scrape_limetorrents(query: str, max_results: int, get_magnets: bool = False) -> list:
    """Scrape LimeTorrents for torrent results"""
    results = []

    try:
        # Launch NOKK with auto_solve for Cloudflare
        server = nokk.launch(auto_solve=True)
        Actor.log.info('NOKK launched successfully')

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(server.ws_endpoint)
            page = await browser.new_page()

            # Build URL
            url = f"https://www.limetorrents.fun/search/all/{query}/seeds/1/"
            Actor.log.info(f'Visiting: {url}')

            await page.goto(url, timeout=60000)
            await page.wait_for_load_state('networkidle', timeout=30000)

            # Extract results
            tables = await page.locator('table.table2').all()
            if not tables:
                Actor.log.warning('No results table found')
                await browser.close()
                return results

            table = tables[0]
            rows = await table.locator('tr').all()

            Actor.log.info(f'Found {len(rows)} rows')

            # Skip header row
            for i in range(1, min(len(rows), max_results + 1)):
                try:
                    row = rows[i]
                    cells = await row.locator('td').all()

                    if len(cells) < 5:
                        continue

                    name_cell = cells[0]
                    name_link = name_cell.locator('a:not(.csprite_dl14)').first
                    name = await name_link.text_content()
                    name = name.strip() if name else ''

                    detail_href = await name_link.get_attribute('href')
                    detail_url = f"https://www.limetorrents.fun{detail_href}" if detail_href else None

                    download_link = name_cell.locator('a.csprite_dl14').first
                    torrent_url = None
                    if await download_link.count() > 0:
                        torrent_url = await download_link.get_attribute('href')

                    size = await cells[2].text_content()
                    size = size.strip() if size else 'Unknown'

                    seeds = await cells[3].text_content()
                    seeds = seeds.strip() if seeds else '0'

                    leeches = await cells[4].text_content()
                    leeches = leeches.strip() if leeches else '0'

                    magnet = None
                    if get_magnets and detail_url:
                        Actor.log.info(f'Extracting magnet for: {name}')
                        await page.goto(detail_url, timeout=60000)
                        await page.wait_for_load_state('networkidle', timeout=30000)
                        magnet_links = await page.locator('a[href^="magnet:"]').all()
                        if magnet_links:
                            magnet = await magnet_links[0].get_attribute('href')
                        # Go back to search results
                        await page.goto(url, timeout=60000)
                        await page.wait_for_load_state('networkidle', timeout=30000)

                    if name and detail_url:
                        result = {
                            'name': name,
                            'detail_url': detail_url,
                            'torrent_url': torrent_url,
                            'seeds': seeds,
                            'leeches': leeches,
                            'size': size,
                            'site': 'limetorrents'
                        }
                        if magnet:
                            result['magnet'] = magnet
                        results.append(result)
                        Actor.log.info(f'Added: {name} ({seeds} seeds)')

                except Exception as e:
                    Actor.log.error(f'Error processing row {i}: {e}')
                    continue

            await browser.close()

    except Exception as e:
        Actor.log.error(f'Error scraping LimeTorrents: {e}')

    return results


if __name__ == '__main__':
    asyncio.run(main())
