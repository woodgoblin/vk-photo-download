# vk-photo-download
Python script for downloading VK users' photo albums and groups' content: photo albums, wall (posts, images including graffiti, comments, polls), discussions (board topics with posts, images, topic-starter polls), and videos.

## Usage
Set `ACCESS_TOKEN`, and optionally `UIDS` and/or `GIDS` in *config.py*, then run:

- `python downloadAlbums.py` — download both groups (first) and people
- `python downloadAlbums.py -g` — groups only
- `python downloadAlbums.py -p` — people only (photo albums)

**Group options:** `--skip-video` (no Videos folder), `--skip-photo` (no photo albums), `--regenerate-wall`, `--regenerate-discussions` to re-fetch and overwrite existing wall or discussions.

**Output:** People under `out/Person Name (id)/Album/`. Groups under `out/groups/Group Name (id)/`: album folders, `wall/` (paginated HTML + images), `discussions/` (one HTML file per topic + images), and `Videos/`. Polls in wall and discussions are rendered with vote counts and progress bars.
