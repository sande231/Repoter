import os
import time
import requests

from agent_sdk import AgentSDK


class MovieListAgent:
    def __init__(self):
        self.agent_id = "movie-list-agent"
        self.sdk = AgentSDK(
            agent_id=self.agent_id,
            ingestion_url="http://localhost:5000",
            tags={"agent_type": "custom", "service": "movie-list"},
        )
        self.api_key = os.environ.get("TMDB_API_KEY")
        self.output_file = "movies_list.html"
        self.poll_interval = 3600  # refresh every hour

    def register(self):
        self.sdk.register({
            "name": "Movie List Agent",
            "type": "web-generator",
            "description": "Fetches popular movies from The Movie Database (TMDB) public API and generates a self-contained HTML page listing them with posters, titles, release dates, and ratings.",
            "version": "1.0.0",
        })

    def fetch_popular_movies(self):
        """
        Fetches the first page of popular movies from the TMDB public REST API.
        Requires a free TMDB API key in the TMDB_API_KEY environment variable.
        API docs: https://developer.themoviedb.org/reference/movie-popular-list
        """
        if not self.api_key:
            raise EnvironmentError(
                "TMDB_API_KEY environment variable is not set. "
                "Register for a free key at https://www.themoviedb.org/settings/api"
            )

        url = "https://api.themoviedb.org/3/movie/popular"
        params = {"api_key": self.api_key, "language": "en-US", "page": 1}
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    def build_html(self, movies):
        """Renders a self-contained HTML page from the list of movie dicts."""
        poster_base = "https://image.tmdb.org/t/p/w300"

        cards_html = ""
        for movie in movies:
            title = movie.get("title", "Unknown Title")
            release = movie.get("release_date", "N/A")
            rating = movie.get("vote_average", 0)
            overview = movie.get("overview", "No description available.")
            poster_path = movie.get("poster_path")
            poster_url = (
                f"{poster_base}{poster_path}"
                if poster_path
                else "https://via.placeholder.com/300x450?text=No+Image"
            )

            # Escape basic HTML special chars for safety
            def esc(s):
                return (
                    str(s)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                )

            cards_html += f"""
            <div class="card">
                <img src="{esc(poster_url)}" alt="Poster for {esc(title)}" loading="lazy">
                <div class="card-body">
                    <h2 class="title">{esc(title)}</h2>
                    <p class="meta">
                        <span class="release">📅 {esc(release)}</span>
                        <span class="rating">⭐ {esc(rating)}/10</span>
                    </p>
                    <p class="overview">{esc(overview)}</p>
                </div>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Popular Movies</title>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0d0d0d;
            color: #f0f0f0;
            min-height: 100vh;
        }}

        header {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            padding: 2rem 1rem;
            text-align: center;
            border-bottom: 2px solid #e94560;
        }}

        header h1 {{
            font-size: 2.5rem;
            color: #e94560;
            letter-spacing: 3px;
            text-transform: uppercase;
        }}

        header p.subtitle {{
            margin-top: 0.5rem;
            color: #aaa;
            font-size: 0.95rem;
        }}

        main {{
            max-width: 1400px;
            margin: 2rem auto;
            padding: 0 1rem;
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 1.5rem;
        }}

        .card {{
            background: #1a1a2e;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            transition: transform 0.25s ease, box-shadow 0.25s ease;
            display: flex;
            flex-direction: column;
        }}

        .card:hover {{
            transform: translateY(-6px);
            box-shadow: 0 10px 30px rgba(233,69,96,0.35);
        }}

        .card img {{
            width: 100%;
            aspect-ratio: 2/3;
            object-fit: cover;
            display: block;
        }}

        .card-body {{
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            flex: 1;
        }}

        .title {{
            font-size: 1rem;
            font-weight: 700;
            color: #ffffff;
            line-height: 1.3;
        }}

        .meta {{
            display: flex;
            justify-content: space-between;
            font-size: 0.8rem;
            color: #aaa;
        }}

        .rating {{
            color: #f5c518;
            font-weight: 600;
        }}

        .overview {{
            font-size: 0.78rem;
            color: #999;
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 4;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        footer {{
            text-align: center;
            padding: 2rem 1rem;
            color: #555;
            font-size: 0.8rem;
            border-top: 1px solid #1a1a2e;
            margin-top: 3rem;
        }}

        footer a {{
            color: #e94560;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <header>
        <h1>🎬 Popular Movies</h1>
        <p class="subtitle">Powered by <a href="https://www.themoviedb.org" style="color:#e94560;text-decoration:none;">TMDB</a> &mdash; auto-refreshed hourly</p>
    </header>
    <main>
        <div class="grid">
            {cards_html}
        </div>
    </main>
    <footer>
        <p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
        <p>Generated by Movie List Agent &bull; <a href="https://www.themoviedb.org">themoviedb.org</a></p>
    </footer>
</body>
</html>
"""
        return html

    def run(self):
        """Fetches movies, builds the HTML page, writes it to disk, and reports metrics."""
        if not self.api_key:
            self.sdk.report_problem(
                "TMDB_API_KEY environment variable is missing. "
                "Set it to a free TMDB API key from https://www.themoviedb.org/settings/api",
                severity="critical",
                details={"required_env_var": "TMDB_API_KEY"},
            )
            return

        try:
            movies = self.fetch_popular_movies()
        except EnvironmentError as exc:
            self.sdk.report_problem(
                f"Environment configuration error: {exc}",
                severity="critical",
                details={"required_env_var": "TMDB_API_KEY"},
            )
            return
        except requests.exceptions.ConnectionError as exc:
            self.sdk.report_problem(
                f"Could not connect to TMDB API: {exc}",
                severity="critical",
                details={"url": "https://api.themoviedb.org/3/movie/popular"},
            )
            return
        except requests.exceptions.HTTPError as exc:
            self.sdk.report_problem(
                f"TMDB API returned an HTTP error: {exc}",
                severity="critical",
                details={"status_code": exc.response.status_code if exc.response else None},
            )
            return
        except requests.exceptions.Timeout:
            self.sdk.report_problem(
                "Request to TMDB API timed out after 15 seconds.",
                severity="warning",
                details={"url": "https://api.themoviedb.org/3/movie/popular"},
            )
            return
        except Exception as exc:
            self.sdk.report_problem(
                f"Unexpected error while fetching movies: {exc}",
                severity="critical",
                details={"exception_type": type(exc).__name__},
            )
            return

        if not movies:
            self.sdk.report_problem(
                "TMDB API returned an empty movie list. The response may be malformed.",
                severity="warning",
                details={"endpoint": "movie/popular"},
            )
            return

        try:
            html_content = self.build_html(movies)
            with open(self.output_file, "w", encoding="utf-8") as fh:
                fh.write(html_content)
        except OSError as exc:
            self.sdk.report_problem(
                f"Could not write HTML file '{self.output_file}': {exc}",
                severity="critical",
                details={"output_file": self.output_file},
            )
            return
        except Exception as exc:
            self.sdk.report_problem(
                f"Unexpected error while building or writing HTML: {exc}",
                severity="critical",
                details={"exception_type": type(exc).__name__},
            )
            return

        self.sdk.send_metrics({
            "movies_fetched": len(movies),
            "output_file": self.output_file,
            "page_size_bytes": len(html_content.encode("utf-8")),
            "poll_interval_seconds": self.poll_interval,
        })


if __name__ == "__main__":
    agent = MovieListAgent()
    agent.register()

    while True:
        agent.run()
        time.sleep(agent.poll_interval)