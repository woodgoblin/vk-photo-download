import urllib.request
import urllib.parse
import urllib.error
import json
import time
import logging

class VkApiException(Exception):
    pass

class VkApi:
    """ VkApi class. Provides some methods for accessing VK API. """
    
    TOO_MANY_REQ_PER_SECOND_ERR = 6
    API_URL    = "https://api.vk.com/method/"
    API_VER    = "5.199"

    def __init__(self, access_token = None):
        """ Constructor
        Args:
            access_token (str): OAuth access token. Will be used for calls if provided.
        Returns:
            VkApi instance.
        """
        self.access_token = access_token

    def call_api(self, method, params):
        """ Just calls VK API
        Args:
            method (str): VK API method, see: https://new.vk.com/dev/methods
            params (dict): Params for given method.
        Returns:
            Response as dict
        """
        
        TRIES = 3
        DELAY = 3

        params["v"] = self.API_VER
        if self.access_token is not None:
            params["access_token"] = self.access_token 

        request_url = self.API_URL + method
        request_data = urllib.parse.urlencode(params, True).encode("ascii")
        logging.debug("requestUrl = %s\nrequestData = %s" % (request_url, request_data.decode("utf-8")))
        
        for try_number in range(1, TRIES + 1):
            try:
                req = urllib.request.Request(request_url, request_data)
                req.add_header('User-Agent', 'Mozilla/5.0')
                with urllib.request.urlopen(req, timeout=10) as conn:
                    response = json.loads(conn.read().decode("utf-8"))
                    logging.debug("response = %s" % response)
                    if "error" in response:
                        if response["error"]["error_code"] == self.TOO_MANY_REQ_PER_SECOND_ERR:
                            logging.info("Too many requests per second, %d sec cooldown." % DELAY)
                            time.sleep(DELAY)
                            continue
                        else:
                            raise VkApiException(response["error"]) 
                    break
            except (urllib.error.URLError, TimeoutError) as e:
                if try_number < TRIES:
                    logging.warning("API request failed (attempt %d/%d): %s. Retrying...", try_number, TRIES, str(e))
                    time.sleep(DELAY)
                    continue
                raise e    

        return response["response"]

    def getUsersByUids(self, uids, fields = ['uid', 'first_name', 'last_name'], nameCase = 'nom'):
        """ Get users by uids
        Args:
            uids (list): List of usres ids
            fields (list): See VK API Docs
            nameCase (str): See VK API Docs
        Returns:
            List of user objects(See VK API Docs) as dict
        """
        MAX_PER_REQUEST = 1000
        users = []
        for offset in range(0, len(uids), MAX_PER_REQUEST):
            users += self.call_api('users.get', {
                'user_ids': ','.join(str(x) for x in uids[offset : offset + MAX_PER_REQUEST]),
                'fields': ','.join(fields),
                'name_case': nameCase})
        return users

    def getUserAlbums(self, ownerId, needSystem = True, needCovers = False):
        """ Get Albums owned by user. For args see VK API Docs
        Args:
            ownerId (str, int)
            needSystem (bool)
            needCovers (bool)
        Returns:
            List of album objects(See VK API Docs) as dict
        """
        time.sleep(0.34)
        return self.call_api('photos.getAlbums', {
            'owner_id': ownerId,
            'need_system': int(needSystem),
            'need_covers': int(needCovers)
            })['items']

    def getPhotosFromAlbum(self, album):
        """ Get photos from VK album
        Args:
            album (dict): Album object (see VK API Docs).
                          Or just {'owner_Id': __ownerId__, 'id': __albumId__}
        Returns:
            List of photo objects (see VK API Docs) as dict
        """
        MAX_PER_REQUEST = 1000
        API_CALL_DELAY = 0.34
        photos = []
        album_id = album['id']
        
        for offset in range(0, album['size'], MAX_PER_REQUEST):
            params = {
                'owner_id': album['owner_id'],
                'offset': offset,
                'count': MAX_PER_REQUEST,
                'photo_sizes': 1
            }
            
            if album_id == -9000:
                params['album_id'] = 'tagged'
            elif album_id == -6:
                params['album_id'] = 'profile'
            elif album_id == -7:
                params['album_id'] = 'wall'
            elif album_id == -15:
                params['album_id'] = 'saved'
            elif album_id >= 0:
                params['album_id'] = album_id
            else:
                params['album_id'] = str(album_id)
            
            try:
                photos += self.call_api('photos.get', params)['items']
            except VkApiException as e:
                error_msg = str(e)
                if 'album_id is invalid' in error_msg and album_id < 0:
                    params.pop('album_id', None)
                    try:
                        photos += self.call_api('photos.get', params)['items']
                    except VkApiException as e2:
                        logging.warning("Cannot get photos from album %s: %s. Trying photos.getUserPhotos...", album_id, str(e2))
                        try:
                            user_photos = self.call_api('photos.getUserPhotos', {
                                'user_id': album['owner_id'],
                                'offset': offset,
                                'count': MAX_PER_REQUEST,
                                'photo_sizes': 1
                            })
                            if 'items' in user_photos:
                                photos += user_photos['items']
                            else:
                                photos += user_photos
                        except VkApiException as e3:
                            logging.error("Failed to get photos from album %s using alternative methods: %s", album_id, str(e3))
                            raise
                else:
                    raise
            
            if offset + MAX_PER_REQUEST < album['size']:
                time.sleep(API_CALL_DELAY)
        return photos

    def getGroupsById(self, group_ids):
        """Get groups by IDs or screen names.
        Args:
            group_ids (list): List of group IDs (int) or screen names (str).
        Returns:
            List of group objects with at least 'id', 'name'.
        """
        if not group_ids:
            return []
        time.sleep(0.34)
        ids_param = ','.join(str(x) for x in group_ids)
        resp = self.call_api('groups.getById', {
            'group_ids': ids_param,
        })
        raw_list = None
        if isinstance(resp, list):
            raw_list = resp
        elif isinstance(resp, dict):
            if 'id' in resp and ('name' in resp or 'title' in resp):
                raw_list = [resp]
            elif 'items' in resp:
                raw_list = resp['items']
            elif 'groups' in resp:
                raw_list = resp['groups']
        if raw_list is not None:
            out = []
            for g in raw_list:
                if not isinstance(g, dict):
                    continue
                g = dict(g)
                if 'id' not in g and 'gid' in g:
                    g['id'] = g['gid']
                if 'id' in g:
                    out.append(g)
            return out
        if resp is None or (isinstance(resp, (int, float)) and resp == 0):
            return []
        keys = list(resp.keys()) if isinstance(resp, dict) else []
        print("DEBUG groups.getById: response type=%s keys=%s" % (type(resp).__name__, keys), flush=True)
        return []

    def getGroupVideos(self, owner_id, count=200):
        """Get all videos owned by a group. owner_id must be negative (e.g. -groupId).
        Args:
            owner_id (int): Group owner_id (negative).
            count (int): Per-request limit (max 200).
        Returns:
            List of video objects (see VK API video.get).
        """
        API_CALL_DELAY = 0.34
        videos = []
        offset = 0
        while True:
            time.sleep(API_CALL_DELAY)
            resp = self.call_api('video.get', {
                'owner_id': owner_id,
                'count': count,
                'offset': offset,
                'extended': 0,
            })
            items = resp.get('items', [])
            total = resp.get('count', 0)
            videos += items
            if not items or offset + len(items) >= total:
                break
            offset += len(items)
        return videos

    def getWall(self, owner_id, count=100, offset=0, extended=1):
        """Get wall posts. owner_id is negative for groups.
        Returns: dict with 'count', 'items', and if extended: 'profiles', 'groups'.
        """
        time.sleep(0.34)
        return self.call_api('wall.get', {
            'owner_id': owner_id,
            'count': count,
            'offset': offset,
            'extended': int(extended),
            'filter': 'all',
        })

    def getWallComments(self, owner_id, post_id, count=100, offset=0, extended=1):
        """Get comments for a wall post. Returns dict with 'count', 'items', 'profiles', 'groups' (if extended)."""
        time.sleep(0.34)
        return self.call_api('wall.getComments', {
            'owner_id': owner_id,
            'post_id': post_id,
            'count': count,
            'offset': offset,
            'extended': int(extended),
            'need_likes': 0,
        })

    def getBoardTopics(self, group_id, count=100, offset=0, extended=1, preview=1, preview_length=1):
        """Get discussion topics. group_id is positive. preview=1 and preview_length=1 return first comment (topic post) with attachments/poll."""
        time.sleep(0.34)
        return self.call_api('board.getTopics', {
            'group_id': group_id,
            'count': count,
            'offset': offset,
            'extended': int(extended),
            'order': 1,
            'preview': int(preview),
            'preview_length': int(preview_length),
        })

    def getBoardComments(self, group_id, topic_id, count=100, offset=0, extended=1):
        """Get posts in a discussion topic. Returns dict with 'count', 'items', 'profiles', 'groups' (if extended)."""
        time.sleep(0.34)
        return self.call_api('board.getComments', {
            'group_id': group_id,
            'topic_id': topic_id,
            'count': count,
            'offset': offset,
            'extended': int(extended),
            'sort': 'asc',
        })