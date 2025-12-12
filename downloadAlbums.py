import os
import urllib.request
import logging
import time
from pprint import pprint
from vkapi import VkApi, VkApiException

import config

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


#logging.basicConfig(level=logging.DEBUG)

api = VkApi(config.ACCESS_TOKEN)
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
        else:
            logging.error("Failed to get albums for user %s (%d): %s", userName, userId, str(e))
            continue
    
    print('%s (%d) - %d albums' % (userName, userId, len(albums)))

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



