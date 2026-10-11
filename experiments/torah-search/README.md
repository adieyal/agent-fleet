# Sefaria explorer

A quick experiment: type a topic and see what Sefaria's public API returns for it.

```sh
python experiments/torah-search/sefaria_search.py serve        # web UI at http://localhost:8765
python experiments/torah-search/sefaria_search.py search "honoring parents"
python experiments/torah-search/sefaria_search.py search "kindness to animals" --json > out.json
```

For each query it runs:

1. **Topics**: `/api/name` finds matching Sefaria topic pages, then `/api/v2/topics/<slug>`
   fetches each topic's description and curated sources.
2. **Full-text search**: `/api/search-wrapper` runs a lemmatized Elasticsearch search
   that matches keywords and their word forms. It doesn't match by meaning.
3. **Texts**: `/api/texts/<ref>` loads the English and Hebrew text of each source.

Sources from all three are merged and de-duplicated. Each source is tagged with where it came from.

**Semantic rerank (optional):** `pip install sentence-transformers`, then pass
`--rerank` or tick the box in the UI. The tool ranks every source by embedding
similarity to your query. This is the only step that is semantic: Sefaria's own
search works on keywords.

Requires Python 3.10+. Without `--rerank` it uses only the standard library.
