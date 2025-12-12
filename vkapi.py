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