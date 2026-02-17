import argparse
import html
import os
import re
import urllib.request
import urllib.error
import logging
import time
from datetime import datetime, timezone
from vkapi import VkApi, VkApiException

import config

VIDEO_REFERER = 'https://vk.com/'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

VIDEO_FILE_QUALITY_ORDER = ('mp4_1080', 'mp4_720', 'mp4_480', 'mp4_360', 'mp4_240')

def sanitize_path_part(name):
    s = re.sub(r'[\\/:*?"<>|]', '_', str(name)).strip().rstrip('. ')
    return s or 'unnamed'

def _safe_text(val):
    """VK can return text as string, tuple, or list. Return non-None str, safe for .strip() and HTML."""
    if val is None:
        return ''
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (list, tuple)):
        return ' '.join(str(t) for t in val).strip()
    return str(val).strip()

def getSingleImageUrl(photo_or_graffiti):
    """Extract best image URL from a single VK photo or graffiti object. Returns URL string or None."""
    if not photo_or_graffiti:
        return None
    candidates = []
    photoKeys = [x for x in photo_or_graffiti.keys() if x.startswith('photo_')]
    if photoKeys:
        try:
            max_size = max(int(x.split('_')[1]) for x in photoKeys)
            url = photo_or_graffiti.get('photo_' + str(max_size))
            if url:
                candidates.append((max_size * max_size, url))
        except (ValueError, KeyError):
            pass
    if photo_or_graffiti.get('url'):
        candidates.append((999999999, photo_or_graffiti['url']))
    if photo_or_graffiti.get('sizes'):
        for size in photo_or_graffiti['sizes']:
            if isinstance(size, dict) and size.get('url'):
                w, h = size.get('width', 0), size.get('height', 0)
                candidates.append((w * h or 1, size['url']))
    for field in ('src_xxbig', 'src_xbig', 'src_big', 'src', 'src_small'):
        if photo_or_graffiti.get(field):
            candidates.append(({'src_xxbig': 5, 'src_xbig': 4, 'src_big': 3, 'src': 2, 'src_small': 1}[field] * 1e6, photo_or_graffiti[field]))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def getPhotoUrls(photos):
    photoUrls = []
    for photoItem in photos:
        candidates = []
        
        if 'url' in photoItem and photoItem['url']:
            candidates.append((999999999, photoItem['url'], 'direct_url'))
        
        photoKeys = [x for x in photoItem.keys() if x.startswith("photo_")]
        if photoKeys:
            maxAvailSize = max([int(x.split('_')[1]) for x in photoKeys])
            url = photoItem['photo_' + str(maxAvailSize)]
            if url:
                candidates.append((maxAvailSize * maxAvailSize, url, 'photo_field'))
        
        if 'sizes' in photoItem and photoItem['sizes']:
            sizeOrder = ['w', 'z', 'y', 'x', 'm', 's']
            availableSizes = {}
            for size in photoItem['sizes']:
                if isinstance(size, dict):
                    if 'type' in size and 'url' in size and size['url']:
                        availableSizes[size['type']] = size
                    elif 'url' in size and size['url']:
                        width = size.get('width', 0)
                        height = size.get('height', 0)
                        resolution = width * height if width > 0 and height > 0 else 1
                        candidates.append((resolution, size['url'], 'sizes_no_type'))
            
            for sizeType in sizeOrder:
                if sizeType in availableSizes:
                    size = availableSizes[sizeType]
                    width = size.get('width', 0)
                    height = size.get('height', 0)
                    resolution = width * height if width > 0 and height > 0 else (1000 if sizeType == 'w' else 500)
                    candidates.append((resolution, size['url'], f'sizes_{sizeType}'))
        
        for field in ['src_xxbig', 'src_xbig', 'src_big', 'src', 'src_small']:
            if field in photoItem and photoItem[field]:
                priority = {'src_xxbig': 5000, 'src_xbig': 4000, 'src_big': 3000, 'src': 2000, 'src_small': 1000}.get(field, 0)
                candidates.append((priority, photoItem[field], field))
        
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            photoUrls.append(candidates[0][1])
        else:
            sizesInfo = "not present"
            if 'sizes' in photoItem:
                if photoItem['sizes']:
                    sizesInfo = f"has {len(photoItem['sizes'])} items: {[list(s.keys()) if isinstance(s, dict) else type(s).__name__ for s in photoItem['sizes'][:3]]}"
                else:
                    sizesInfo = "empty list"
            logging.warning("No photo URL found for photo %s. Available keys: %s. Sizes: %s", 
                          photoItem.get('id', 'unknown'), list(photoItem.keys()), sizesInfo)
    return photoUrls

def downloadPhotos(outDir, urls):
    if not os.path.exists(outDir):
        os.makedirs(outDir)
    
    MAX_RETRIES = 3
    RETRY_DELAY = 2
    DOWNLOAD_DELAY = 0.1
    DOWNLOAD_TIMEOUT = 10
    
    total = len(urls)
    for idx, photoUrl in enumerate(urls):
        urlWithoutParams = photoUrl.split('?')[0]
        filename = urlWithoutParams.rsplit('/', 1)[-1]
        if not filename.endswith('.jpg') and not filename.endswith('.jpeg') and not filename.endswith('.png'):
            if '.' in filename:
                filename = filename.rsplit('.', 1)[0] + '.jpg'
            else:
                filename = filename + '.jpg'
        file = os.path.join(outDir, filename)
        
        if os.path.exists(file):
            continue
        
        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print('        [%d/%d] Downloading: %s' % (idx + 1, total, filename))
                start_time = time.time()
                
                req = urllib.request.Request(photoUrl)
                req.add_header('User-Agent', 'Mozilla/5.0')
                with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
                    with open(file, 'wb') as out_file:
                        out_file.write(response.read())
                
                elapsed = time.time() - start_time
                file_size = os.path.getsize(file) / (1024 * 1024)
                print('        [%d/%d] Downloaded: %s (%.2f MB in %.1f s)' % (idx + 1, total, filename, file_size, elapsed))
                success = True
                break
            except (urllib.error.URLError, OSError, IOError, TimeoutError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY * attempt
                    logging.warning("Download failed for %s (attempt %d/%d): %s. Retrying in %d seconds...", 
                                  filename, attempt, MAX_RETRIES, str(e), delay)
                    time.sleep(delay)
                else:
                    logging.error("Failed to download %s after %d attempts: %s", filename, MAX_RETRIES, str(e))
        
        if idx < len(urls) - 1:
            time.sleep(DOWNLOAD_DELAY)

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None

def _open_video_url(url, timeout=60):
    """Open VK video URL. Follow at most one redirect manually to avoid VK's redirect loop."""
    def make_req(u):
        r = urllib.request.Request(u)
        r.add_header('User-Agent', USER_AGENT)
        r.add_header('Referer', VIDEO_REFERER)
        return r
    opener = urllib.request.build_opener(_NoRedirectHandler())
    req = make_req(url)
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get('Location')
            if loc:
                try:
                    return opener.open(make_req(loc), timeout=timeout)
                except urllib.error.HTTPError:
                    pass
        raise e

def getVideoUrl(video_item):
    """Pick best available direct file URL from VK video object. Returns (url, suggested_basename) or (None, None)."""
    vid = video_item.get('id', 'v')
    title = sanitize_path_part(_safe_text(video_item.get('title')) or str(vid))[:80]
    base = '%s_%s.mp4' % (vid, title)
    base = base.strip('. ') or ('%s.mp4' % vid)
    direct = video_item.get('direct_url')
    if direct and isinstance(direct, str) and direct.strip():
        return (direct.strip(), base)
    files = video_item.get('files') or {}
    for q in VIDEO_FILE_QUALITY_ORDER:
        url = files.get(q)
        if url and isinstance(url, str) and url.strip():
            return (url.strip(), base)
    logging.warning("No video file URL for video id=%s (owner_id=%s). Keys: %s",
                    vid, video_item.get('owner_id'), list(video_item.keys()))
    return (None, None)

def downloadVideos(outDir, video_items):
    """Download videos from list of VK video items. Skips items with no file URL."""
    if not os.path.exists(outDir):
        os.makedirs(outDir)
    urls_with_names = []
    for v in video_items:
        url, base = getVideoUrl(v)
        if url:
            urls_with_names.append((url, base))
    MAX_RETRIES = 3
    RETRY_DELAY = 2
    DOWNLOAD_DELAY = 0.5
    DOWNLOAD_TIMEOUT = 60
    total = len(urls_with_names)
    for idx, (video_url, base_name) in enumerate(urls_with_names):
        file = os.path.join(outDir, base_name)
        if os.path.exists(file):
            continue
        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print('        [%d/%d] Downloading: %s' % (idx + 1, total, base_name))
                with _open_video_url(video_url, timeout=DOWNLOAD_TIMEOUT) as response:
                    with open(file, 'wb') as out_file:
                        out_file.write(response.read())
                print('        [%d/%d] Downloaded: %s' % (idx + 1, total, base_name))
                success = True
                break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, IOError, TimeoutError) as e:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                else:
                    logging.error("Failed to download %s after %d attempts: %s", base_name, MAX_RETRIES, str(e))
        if idx < total - 1:
            time.sleep(DOWNLOAD_DELAY)

def _merge_profiles_groups(acc, resp):
    for p in resp.get('profiles') or []:
        acc['profiles'][p['id']] = p
    for g in resp.get('groups') or []:
        acc['groups'][g['id']] = g

def _resolve_author(from_id, profiles, groups, cache=None):
    """Resolve from_id to display name. If cache dict is provided, use it to avoid recomputing."""
    if cache is not None and from_id in cache:
        return cache[from_id]
    if not from_id:
        result = 'Unknown'
    elif from_id > 0:
        p = profiles.get(from_id)
        if p:
            first = _safe_text(p.get('first_name'))
            last = _safe_text(p.get('last_name'))
            name = ('%s %s' % (first, last)).strip() or ('User %s' % from_id)
            result = '%s (%s)' % (name, from_id)
        else:
            result = 'User %s' % from_id
    else:
        gid = -from_id
        g = groups.get(gid)
        if g:
            name = _safe_text(g.get('name')) or ('Group %s' % gid)
            result = '%s (%s)' % (name, gid)
        else:
            result = 'Group %s' % gid
    if cache is not None:
        cache[from_id] = result
    return result

def _download_one_image(url, filepath, timeout=15):
    if os.path.exists(filepath):
        return True
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                with open(filepath, 'wb') as f:
                    f.write(resp.read())
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(2 * attempt)
            else:
                logging.warning("Failed to download wall image %s: %s", filepath, e)
    return False

WALL_POSTS_PER_PAGE = 25

def _parse_poll_object(poll):
    """Convert a single VK poll object to our format. Returns one dict or None."""
    if not poll or not isinstance(poll, dict):
        return None
    question = _safe_text(poll.get('question') or poll.get('title'))
    answers = []
    for a in (poll.get('answers') or []):
        text = _safe_text(a.get('text'))
        votes = a.get('votes', 0) if isinstance(a.get('votes'), (int, float)) else 0
        answers.append({'text': text, 'votes': int(votes)})
    return {'question': question or 'Poll', 'answers': answers}

def _extract_polls(attachments):
    """From VK attachments list, return list of poll dicts: [{question, answers: [{text, votes}]}]."""
    if not attachments:
        return []
    out = []
    for att in attachments:
        atype = att.get('type')
        if atype not in ('poll', 'vote'):
            continue
        poll = att.get('poll') or att.get('vote')
        p = _parse_poll_object(poll)
        if p:
            out.append(p)
    return out

def _extract_polls_from_post(post):
    """Extract all polls from a post/comment: from attachments and from top-level 'poll'/'vote' if present."""
    polls = _extract_polls(post.get('attachments') or [])
    for key in ('poll', 'vote'):
        top_poll = _parse_poll_object(post.get(key))
        if top_poll:
            polls = [top_poll] + polls
            break
    return polls

def _render_polls_html(polls):
    """Return HTML fragment for poll(s) with progress bars for vote share."""
    if not polls:
        return ''
    parts = []
    for poll in polls:
        total = sum(a['votes'] for a in poll['answers'])
        parts.append('<div class="poll"><div class="poll-question">%s</div><ul class="poll-answers">' % html.escape(poll['question']))
        for a in poll['answers']:
            votes = a['votes']
            pct = (votes / total * 100) if total else 0
            parts.append(
                '<li class="poll-answer">'
                '<span class="poll-answer-label">%s</span>'
                '<span class="poll-answer-meta">%s (%.1f%%)</span>'
                '<div class="poll-bar-wrap"><div class="poll-bar" style="width:%.2f%%"></div></div>'
                '</li>' % (html.escape(a['text']), votes, pct, pct)
            )
        parts.append('</ul></div>')
    return ''.join(parts)

def crawl_wall(api, group, base_dir, regenerate=False):
    gid = group['id']
    owner_id = -gid
    group_name = group.get('name') or ('Group %d' % gid)
    wall_dir = os.path.join(base_dir, 'wall')
    if not regenerate and os.path.exists(os.path.join(wall_dir, 'index.html')):
        print('    Wall - already exists, skipping', flush=True)
        return
    images_dir = os.path.join(wall_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)
    print('      Wall: fetching...', flush=True)
    all_posts = []
    profiles = {}
    groups_dict = {}
    offset = 0
    count = 100
    total_count = None
    while True:
        try:
            resp = api.getWall(owner_id, count=count, offset=offset, extended=1)
        except VkApiException as e:
            logging.error("Wall get failed for group %s (%d): %s", group_name, gid, e)
            return
        items = resp.get('items') or []
        _merge_profiles_groups({'profiles': profiles, 'groups': groups_dict}, resp)
        if not items:
            break
        all_posts.extend(items)
        total_count = resp.get('count', 0)
        print('      Wall: fetched %d/%d posts' % (len(all_posts), total_count), flush=True)
        if offset + len(items) >= total_count:
            break
        offset += len(items)
    if not all_posts:
        print('    Wall - 0 posts')
        return
    print('    Wall - %d posts' % len(all_posts))
    posts_rendered = []
    num_posts = len(all_posts)
    author_cache = {}
    for post_idx, post in enumerate(all_posts):
        if (post_idx + 1) % 100 == 0 or post_idx == 0 or post_idx == num_posts - 1:
            print('      Wall: processing post %d/%d' % (post_idx + 1, num_posts), flush=True)
        author_name = _resolve_author(post.get('from_id', 0), profiles, groups_dict, author_cache)
        post_date = datetime.fromtimestamp(post.get('date', 0), tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        text = _safe_text(post.get('text'))
        repost_link = None
        if post.get('copy_history'):
            first = post['copy_history'][0]
            repost_link = 'https://vk.com/wall%s_%s' % (first.get('owner_id', ''), first.get('id', ''))
        image_paths = []
        post_attachments = post.get('attachments') or []
        polls_list = _extract_polls_from_post(post)
        for idx, att in enumerate(post_attachments):
            atype = att.get('type')
            obj = None
            if atype == 'photo':
                obj = att.get('photo')
            elif atype == 'graffiti':
                obj = att.get('graffiti')
            if not obj:
                continue
            url = getSingleImageUrl(obj)
            if not url:
                continue
            ext = '.jpg'
            if '.png' in url.split('?')[0].lower():
                ext = '.png'
            fname = 'post_%s_%s%s' % (post.get('id'), idx, ext)
            filepath = os.path.join(images_dir, fname)
            if _download_one_image(url, filepath):
                image_paths.append('images/' + fname)
        comments_list = []
        comment_count = (post.get('comments') or {}).get('count', -1)
        if comment_count != 0:
            post_id = post.get('id')
            com_offset = 0
            com_count = 100
            while True:
                try:
                    cresp = api.getWallComments(owner_id, post_id, count=com_count, offset=com_offset, extended=1)
                except VkApiException:
                    break
                _merge_profiles_groups({'profiles': profiles, 'groups': groups_dict}, cresp)
                citems = cresp.get('items') or []
                for c in citems:
                    if c.get('deleted'):
                        continue
                    cfrom = c.get('from_id', 0)
                    cauthor = _resolve_author(cfrom, profiles, groups_dict, author_cache)
                    cdate = datetime.fromtimestamp(c.get('date', 0), tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
                    comment_polls = _extract_polls_from_post(c)
                    comments_list.append({'author': cauthor, 'date': cdate, 'text': _safe_text(c.get('text')), 'polls': comment_polls})
                if not citems or com_offset + len(citems) >= cresp.get('count', 0):
                    break
                com_offset += len(citems)
        posts_rendered.append({
            'author': author_name,
            'date': post_date,
            'text': text,
            'image_paths': image_paths,
            'repost_link': repost_link,
            'polls': polls_list,
            'comments': comments_list,
        })
    num_pages = (len(posts_rendered) + WALL_POSTS_PER_PAGE - 1) // WALL_POSTS_PER_PAGE
    if num_pages > 1:
        print('      Wall: writing %d HTML pages...' % num_pages, flush=True)
    for page_one in range(1, num_pages + 1):
        if num_pages > 5 and (page_one == 1 or page_one % 10 == 0 or page_one == num_pages):
            print('      Wall: page %d/%d' % (page_one, num_pages), flush=True)
        start = (page_one - 1) * WALL_POSTS_PER_PAGE
        chunk = posts_rendered[start:start + WALL_POSTS_PER_PAGE]
        prev_link = ('wall_%s.html' % (page_one - 1)) if page_one > 1 else None
        next_link = ('wall_%s.html' % (page_one + 1)) if page_one < num_pages else None
        html_parts = [
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Wall %s - %s</title>' % (page_one, html.escape(group_name)),
            '<style>body{font-family:sans-serif;max-width:800px;margin:1em auto;padding:0 1em;}',
            '.post{margin:1.5em 0;border-bottom:1px solid #ccc;}',
            '.meta{color:#666;font-size:0.9em;}',
            '.comments{margin-left:1.5em;margin-top:0.5em;}',
            '.comment{margin:0.3em 0;font-size:0.95em;}',
            '.poll{margin:1em 0;padding:0.8em;background:#f5f5f5;border-radius:8px;}',
            '.poll-question{font-weight:bold;margin-bottom:0.6em;}',
            '.poll-answers{list-style:none;padding:0;margin:0;}',
            '.poll-answer{margin:0.5em 0;}',
            '.poll-answer-label{display:block;font-size:0.95em;}',
            '.poll-answer-meta{color:#666;font-size:0.85em;}',
            '.poll-bar-wrap{height:8px;background:#e0e0e0;border-radius:4px;overflow:hidden;margin-top:2px;}',
            '.poll-bar{height:100%;background:#4a76a8;border-radius:4px;transition:width 0.3s;}',
            'img{max-width:100%;height:auto;}</style></head><body>',
            '<h1>%s — Wall (page %d of %d)</h1>' % (html.escape(group_name), page_one, num_pages),
            '<p>',
        ]
        if prev_link:
            html_parts.append('<a href="%s">← Previous</a> ' % html.escape(prev_link))
        if next_link:
            html_parts.append('<a href="%s">Next →</a>' % html.escape(next_link))
        html_parts.append('</p>')
        for p in chunk:
            html_parts.append('<article class="post">')
            html_parts.append('<div class="meta">%s · %s</div>' % (html.escape(p['author']), html.escape(p['date'])))
            if p['text']:
                html_parts.append('<div class="text">%s</div>' % html.escape(p['text']).replace('\n', '<br>\n'))
            for path in p['image_paths']:
                html_parts.append('<p><img src="%s" alt=""></p>' % html.escape(path))
            if p['repost_link']:
                html_parts.append('<p><a href="%s">Repost: original</a></p>' % html.escape(p['repost_link']))
            if p.get('polls'):
                html_parts.append(_render_polls_html(p['polls']))
            if p['comments']:
                html_parts.append('<div class="comments">')
                for c in p['comments']:
                    html_parts.append('<div class="comment"><span class="meta">%s · %s</span> %s' % (
                        html.escape(c['author']), html.escape(c['date']), html.escape(c['text']).replace('\n', '<br>\n')))
                    if c.get('polls'):
                        html_parts.append(_render_polls_html(c['polls']))
                    html_parts.append('</div>')
                html_parts.append('</div>')
            html_parts.append('</article>')
        html_parts.append('<p>')
        if prev_link:
            html_parts.append('<a href="%s">← Previous</a> ' % html.escape(prev_link))
        if next_link:
            html_parts.append('<a href="%s">Next →</a>' % html.escape(next_link))
        html_parts.append('</p></body></html>')
        out_path = os.path.join(wall_dir, 'wall_%s.html' % page_one)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(''.join(html_parts))
    index_path = os.path.join(wall_dir, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=wall_1.html">')
        f.write('<title>Wall - %s</title></head><body><a href="wall_1.html">Wall — page 1</a></body></html>' % html.escape(group_name))
    print('    Wall → %s' % os.path.join(wall_dir, 'index.html'))

DISCUSSION_POSTS_PER_PAGE = 25

def crawl_discussions(api, group, base_dir, regenerate=False):
    gid = group['id']
    group_name = group.get('name') or ('Group %d' % gid)
    discussions_dir = os.path.join(base_dir, 'discussions')
    if not regenerate and os.path.exists(os.path.join(discussions_dir, 'index.html')):
        print('    Discussions - already exist, skipping', flush=True)
        return
    print('      Discussions: fetching topics...', flush=True)
    all_topics = []
    profiles = {}
    groups_dict = {}
    offset = 0
    count = 100
    while True:
        try:
            resp = api.getBoardTopics(gid, count=count, offset=offset, extended=1)
        except VkApiException as e:
            logging.error("Board getTopics failed for group %s (%d): %s", group_name, gid, e)
            return
        items = resp.get('items') or []
        _merge_profiles_groups({'profiles': profiles, 'groups': groups_dict}, resp)
        if not items:
            break
        all_topics.extend(items)
        total = resp.get('count', 0)
        if offset + len(items) >= total:
            break
        offset += len(items)
    if not all_topics:
        print('    Discussions - 0 topics')
        os.makedirs(discussions_dir, exist_ok=True)
        with open(os.path.join(discussions_dir, 'index.html'), 'w', encoding='utf-8') as f:
            f.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Discussions - %s</title></head><body><h1>Discussions</h1><p>No topics.</p></body></html>' % html.escape(group_name))
        return
    print('    Discussions - %d topics' % len(all_topics))
    os.makedirs(discussions_dir, exist_ok=True)
    author_cache = {}
    index_links = []
    for t_idx, topic in enumerate(all_topics):
        topic_id = topic.get('id')
        topic_title = _safe_text(topic.get('title')) or ('Topic %s' % topic_id)
        safe_title = sanitize_path_part(topic_title)[:60]
        topic_dir = os.path.join(discussions_dir, 'topic_%s_%s' % (topic_id, safe_title))
        topic_images_dir = os.path.join(topic_dir, 'images')
        os.makedirs(topic_dir, exist_ok=True)
        os.makedirs(topic_images_dir, exist_ok=True)
        all_posts = []
        topic_starter_poll = None
        com_offset = 0
        com_count = 100
        while True:
            try:
                cresp = api.getBoardComments(gid, topic_id, count=com_count, offset=com_offset, extended=1)
            except VkApiException:
                break
            _merge_profiles_groups({'profiles': profiles, 'groups': groups_dict}, cresp)
            if com_offset == 0 and cresp.get('poll'):
                topic_starter_poll = _parse_poll_object(cresp['poll'])
            items = cresp.get('items') or []
            if not items:
                break
            all_posts.extend(items)
            if com_offset + len(items) >= cresp.get('count', 0):
                break
            com_offset += len(items)
        if (t_idx + 1) % 10 == 0 or t_idx == 0 or t_idx == len(all_topics) - 1:
            print('      Discussions: topic %d/%d "%s" (%d posts)' % (t_idx + 1, len(all_topics), topic_title[:40], len(all_posts)), flush=True)
        posts_rendered = []
        for post in all_posts:
            author_name = _resolve_author(post.get('from_id', 0), profiles, groups_dict, author_cache)
            post_date = datetime.fromtimestamp(post.get('date', 0), tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
            text = _safe_text(post.get('text'))
            post_attachments = post.get('attachments') or []
            polls_list = _extract_polls_from_post(post)
            image_paths = []
            for idx, att in enumerate(post_attachments):
                atype = att.get('type')
                obj = att.get('photo') if atype == 'photo' else (att.get('graffiti') if atype == 'graffiti' else None)
                if not obj:
                    continue
                url = getSingleImageUrl(obj)
                if not url:
                    continue
                ext = '.png' if '.png' in url.split('?')[0].lower() else '.jpg'
                fname = 'post_%s_%s%s' % (post.get('id'), idx, ext)
                filepath = os.path.join(topic_images_dir, fname)
                if _download_one_image(url, filepath):
                    image_paths.append('images/' + fname)
            posts_rendered.append({'author': author_name, 'date': post_date, 'text': text, 'image_paths': image_paths, 'polls': polls_list})
        if posts_rendered:
            topic_polls = []
            if topic_starter_poll:
                topic_polls.append(topic_starter_poll)
            for key in ('poll', 'vote'):
                if topic.get(key):
                    p = _parse_poll_object(topic[key])
                    if p:
                        topic_polls.append(p)
                        break
            preview = topic.get('preview') or {}
            first_comment = preview.get('first_comment') if isinstance(preview, dict) else None
            if isinstance(first_comment, dict):
                topic_polls = _extract_polls_from_post(first_comment) + topic_polls
            if topic_polls:
                posts_rendered[0]['polls'] = topic_polls + (posts_rendered[0].get('polls') or [])
            if isinstance(topic.get('first_comment'), str) and not posts_rendered[0].get('text'):
                posts_rendered[0]['text'] = topic['first_comment']
            if not posts_rendered[0].get('polls') and not posts_rendered[0].get('text') and not posts_rendered[0].get('image_paths') and all_posts:
                posts_rendered[0]['text'] = '(Опрос / первый пост темы не возвращается API для этой темы.)'
        num_pages = (len(posts_rendered) + DISCUSSION_POSTS_PER_PAGE - 1) // DISCUSSION_POSTS_PER_PAGE
        topic_slug = 'topic_%s_%s' % (topic_id, safe_title)
        index_links.append((topic_title, topic_slug))
        for page_one in range(1, num_pages + 1):
            start = (page_one - 1) * DISCUSSION_POSTS_PER_PAGE
            chunk = posts_rendered[start:start + DISCUSSION_POSTS_PER_PAGE]
            prev_link = ('topic_%s.html' % (page_one - 1)) if page_one > 1 else None
            next_link = ('topic_%s.html' % (page_one + 1)) if page_one < num_pages else None
            html_parts = [
                '<!DOCTYPE html><html><head><meta charset="utf-8"><title>%s - page %d</title>' % (html.escape(topic_title), page_one),
                '<style>body{font-family:sans-serif;max-width:800px;margin:1em auto;padding:0 1em;}',
                '.post{margin:1.5em 0;border-bottom:1px solid #ccc;}.meta{color:#666;font-size:0.9em;}',
                '.poll{margin:1em 0;padding:0.8em;background:#f5f5f5;border-radius:8px;}',
                '.poll-question{font-weight:bold;margin-bottom:0.6em;}',
                '.poll-answers{list-style:none;padding:0;margin:0;}',
                '.poll-answer{margin:0.5em 0;}',
                '.poll-answer-label{display:block;font-size:0.95em;}',
                '.poll-answer-meta{color:#666;font-size:0.85em;}',
                '.poll-bar-wrap{height:8px;background:#e0e0e0;border-radius:4px;overflow:hidden;margin-top:2px;}',
                '.poll-bar{height:100%;background:#4a76a8;border-radius:4px;transition:width 0.3s;}',
                'img{max-width:100%;height:auto;}</style></head><body>',
                '<h1>%s</h1><p>Group: %s — page %d of %d</p>' % (html.escape(topic_title), html.escape(group_name), page_one, num_pages),
                '<p>',
            ]
            if prev_link:
                html_parts.append('<a href="%s">← Previous</a> ' % html.escape(prev_link))
            if next_link:
                html_parts.append('<a href="%s">Next →</a>' % html.escape(next_link))
            html_parts.append(' <a href="../index.html">↑ Discussions</a></p>')
            for p in chunk:
                html_parts.append('<article class="post">')
                html_parts.append('<div class="meta">%s · %s</div>' % (html.escape(p['author']), html.escape(p['date'])))
                if p['text']:
                    html_parts.append('<div class="text">%s</div>' % html.escape(p['text']).replace('\n', '<br>\n'))
                for path in p['image_paths']:
                    html_parts.append('<p><img src="%s" alt=""></p>' % html.escape(path))
                if p.get('polls'):
                    html_parts.append(_render_polls_html(p['polls']))
                html_parts.append('</article>')
            html_parts.append('<p>')
            if prev_link:
                html_parts.append('<a href="%s">← Previous</a> ' % html.escape(prev_link))
            if next_link:
                html_parts.append('<a href="%s">Next →</a>' % html.escape(next_link))
            html_parts.append(' <a href="../index.html">↑ Discussions</a></p></body></html>')
            with open(os.path.join(topic_dir, 'topic_%s.html' % page_one), 'w', encoding='utf-8') as f:
                f.write(''.join(html_parts))
        with open(os.path.join(topic_dir, 'index.html'), 'w', encoding='utf-8') as f:
            f.write('<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=topic_1.html">')
            f.write('<title>%s</title></head><body><a href="topic_1.html">%s</a></body></html>' % (html.escape(topic_title), html.escape(topic_title)))
    os.makedirs(discussions_dir, exist_ok=True)
    with open(os.path.join(discussions_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Discussions - %s</title>' % html.escape(group_name))
        f.write('<style>body{font-family:sans-serif;max-width:800px;margin:1em auto;padding:0 1em;} ul{line-height:1.8;}</style></head><body>')
        f.write('<h1>Discussions — %s</h1><ul>' % html.escape(group_name))
        for title, slug in index_links:
            f.write('<li><a href="%s/">%s</a></li>' % (html.escape(slug), html.escape(title)))
        f.write('</ul></body></html>')
    print('    Discussions → %s' % os.path.join(discussions_dir, 'index.html'))


def run_people(api, skip_photo=False):
    users = api.getUsersByUids(config.UIDS)
    for user in users:
        userId = user['id']
        userName = '%s %s' % (user['first_name'], user['last_name'])
        try:
            albums = api.getUserAlbums(userId)
        except VkApiException as e:
            error_info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
            error_code = error_info.get('error_code', 0)
            if error_code == 30:
                print('%s (%d) - Profile is private, skipping' % (userName, userId))
                continue
            logging.error("Failed to get albums for user %s (%d): %s", userName, userId, str(e))
            continue
        print('%s (%d) - %d albums' % (userName, userId, len(albums)))
        if not skip_photo:
            for album in albums:
                outDir = '%s/%s (%d)/%s' % (config.OUT, userName, userId, album['title'] + ' (' + str(album['id']) + ')')
                print('    "%s" - %d photos' % (album['title'], album['size']))
                try:
                    photoUrls = getPhotoUrls(api.getPhotosFromAlbum(album))
                    downloadPhotos(outDir, photoUrls)
                except VkApiException as e:
                    logging.error("Failed to get photos from album '%s' for user %s (%d): %s",
                                  album.get('title', 'unknown'), userName, userId, str(e))
                    continue

def run_groups(api, skip_video=False, skip_photo=False, regenerate_wall=False, regenerate_discussions=False):
    if not config.GIDS:
        print('No group IDs in config.GIDS, skipping groups.', flush=True)
        return
    print('Groups: resolving %d ID(s)...' % len(config.GIDS), flush=True)
    groups = api.getGroupsById(config.GIDS)
    if not groups:
        print('No groups found (check GIDS and token scope).', flush=True)
        return
    for group in groups:
        gid = group['id']
        owner_id = -gid
        group_name = group.get('name') or ('Group %d' % gid)
        safe_name = sanitize_path_part('%s (%d)' % (group_name, gid))
        base_dir = os.path.join(config.OUT, 'groups', safe_name)
        try:
            albums = api.getUserAlbums(owner_id)
        except VkApiException as e:
            error_info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
            error_code = error_info.get('error_code', 0)
            if error_code == 30 or error_code == 15:
                print('%s (%d) - Access denied, skipping' % (group_name, gid))
                continue
            logging.error("Failed to get albums for group %s (%d): %s", group_name, gid, str(e))
            continue
        print('%s (%d) - %d albums' % (group_name, gid, len(albums)))
        if not skip_photo:
            for album in albums:
                outDir = os.path.join(base_dir, sanitize_path_part(album['title']) + ' (' + str(album['id']) + ')')
                print('    "%s" - %d photos' % (album['title'], album['size']))
                try:
                    photoUrls = getPhotoUrls(api.getPhotosFromAlbum(album))
                    downloadPhotos(outDir, photoUrls)
                except VkApiException as e:
                    logging.error("Failed to get photos from album '%s' for group %s (%d): %s",
                                  album.get('title', 'unknown'), group_name, gid, str(e))
                    continue
        try:
            crawl_wall(api, group, base_dir, regenerate=regenerate_wall)
        except Exception as e:
            logging.error("Wall crawl failed for group %s (%d): %s", group_name, gid, e)
        try:
            crawl_discussions(api, group, base_dir, regenerate=regenerate_discussions)
        except Exception as e:
            logging.error("Discussions crawl failed for group %s (%d): %s", group_name, gid, e)
        if not skip_video:
            try:
                videos = api.getGroupVideos(owner_id)
                if videos:
                    print('    Videos - %d' % len(videos))
                    videos_dir = os.path.join(base_dir, 'Videos')
                    downloadVideos(videos_dir, videos)
            except VkApiException as e:
                logging.error("Failed to get videos for group %s (%d): %s", group_name, gid, str(e))

def main():
    parser = argparse.ArgumentParser(description='Download VK photos (and group videos) from config UIDS/GIDS.')
    parser.add_argument('-g', '--groups-only', action='store_true', help='Download groups only')
    parser.add_argument('-p', '--people-only', action='store_true', help='Download people only')
    parser.add_argument('--skip-video', action='store_true', help='Skip downloading group videos')
    parser.add_argument('--skip-photo', action='store_true', help='Skip downloading photo albums (wall images/graffiti still downloaded)')
    parser.add_argument('--regenerate-wall', action='store_true', help='Overwrite existing wall HTML/images')
    parser.add_argument('--regenerate-discussions', action='store_true', help='Overwrite existing discussions HTML/images')
    args = parser.parse_args()
    if args.groups_only and args.people_only:
        parser.error('Use either -g or -p, not both.')
    api = VkApi(config.ACCESS_TOKEN)
    do_groups = args.groups_only or (not args.people_only and not args.groups_only)
    do_people = args.people_only or (not args.groups_only and not args.people_only)
    if do_groups:
        run_groups(api, skip_video=args.skip_video, skip_photo=args.skip_photo,
                   regenerate_wall=args.regenerate_wall, regenerate_discussions=args.regenerate_discussions)
    if do_people:
        run_people(api, skip_photo=args.skip_photo)

if __name__ == '__main__':
    main()

