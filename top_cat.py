#!/usr/bin/env python3

"""
Nick Hahner 2017-2020
Ever wanted to automate finding the top cute cat picture from /r/aww to send to your friends?
All you need to do is run ./top_cat.py and your life will change forever.

You'll need to create ~/.top_cat/config.toml if you want to find dogs instead!
You can also add your slack api key to ^ if you want a nifty slack integration.
All items in the config file are optional. See example config for defaults.

Usage:
    top_cat.py [options]

Options:
    -h, --help            Show this help message and exit
    -v, --verbose         Debug info
    -c, --config FILE     config file location [default: ~/.top_cat/config.toml]
    -d, --db-file FILE    sqlite3 db file location. Default in toml file.

"""

import time
import requests
import re
# import facebook
import sys
import json
import toml
import sqlite3
import hashlib
import os
from time import sleep
from PIL import Image
from io import BytesIO, StringIO
from tempfile import NamedTemporaryFile, TemporaryDirectory
import pyjq
# # Because the reddit api links use escaped html strings ie &amp;
# from xml.sax.saxutils import unescape
from docopt import docopt

from collections import Counter

import cv2
import numpy as np
import cv2
from matplotlib import pyplot as plt
from PIL import Image
import string
import random
import pprint
import difflib
import aiosql

# NOTE: Maybe add this to the config?
MAX_IMS_PER_VIDEO = 10

# A little trial and error got me this cutoff. Maybe change it for a different model type?
# SCORE_CUTOFF = .05  # For deeplabv3
SCORE_CUTOFF = .5    # For google vision api

import mimetypes

# So we can copy paste into ipython for debugging. Assuming we run ipython from the repo dir
if hasattr(__builtins__,'__IPYTHON__'):
    THIS_SCRIPT_DIR = os.getcwd()
else:
    THIS_SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
QUERIES = aiosql.from_path(THIS_SCRIPT_DIR+"/sql", "sqlite3")


def get_config(config_file_loc="~/.top_cat/config.toml"):
    default_config = toml.load(THIS_SCRIPT_DIR+'/top_cat_default.toml')

    user_config_file_loc = os.path.expanduser(config_file_loc)
    # Let's query the config file
    if os.path.isfile(user_config_file_loc):
        try:
            top_cat_user_config = toml.load(user_config_file_loc)
        except Exception as e:
            print( "Malformed config file at '%s'" % (config_file_loc) , file=sys.stderr)
            exit(1)
    else:
        top_cat_user_config = dict()

    # Make sure to crash if any config options are specified that we don't know what to do with
    available_opts = list(default_config.keys())
    for user_config_opt in top_cat_user_config.keys():
        assert user_config_opt in available_opts, f"# ERROR: You specified an unsopported option: {user_config_opt} \n# Maybe you meant {available_opts[np.argmax([difflib.SequenceMatcher(None, user_config_opt, opt).ratio() for opt in available_opts])]}\n# Possible choices: {available_opts}"

    top_cat_config = {**default_config, **top_cat_user_config}

    # If we plan on posting to social media, let's make sure we have tokens to try
    assert ( not top_cat_config['POST_TO_SLACK_TF']
                or (top_cat_config['POST_TO_SLACK_TF'] and top_cat_config['SLACK_API_TOKEN'] and top_cat_config['SLACK_API_TOKEN'] != 'YOUR__SLACK__API_TOKEN_GOES_HERE')
            ), "If you want to post to slack then you need to add an api key to the config file!"
    assert ( not top_cat_config['POST_TO_FB_TF']
                or (top_cat_config['POST_TO_FB_TF'] and top_cat_config['FB_PAGE_ACCESS_TOKEN'] and top_cat_config['FB_PAGE_ACCESS_TOKEN'] != 'YOUR__FB__PAGE_ACCESS_TOKEN_GOES_HERE')
            ), "If you want to post to FB then you need to add a fb page_access_token to the config"
    return top_cat_config


def guarantee_tables_exist(db_conn):
    QUERIES.create_tables_and_indexes(db_conn)
    db_conn.commit()


def fix_imgur_url(url):
    """
    Sometimes people post imgur urls without the image or video extension.
    This grabs the extension and fixes the link so we go straight to the image or video:
    eg "http://i.imgur.com/mc316Un" -> "http://i.imgur.com/mc316Un.jpg"
    """
    if "imgur.com" in url:
        # Don't bother doing anything fancy if it already ends in .jpg etc
        if '.' not in url.split("/")[-1]:
            imgur_id = re.findall('imgur.com/([^.]+)',url)[0]
            req = requests.get(url)
            possible_media_links = set(re.findall(r'content="(http.{0,50}%s\.[^"?]+)[?"]'%imgur_id, req.text))
            assert possible_media_links, "can't find the media links in imgur url " + url
            suffix_mapto_link = dict(zip([l.split('.')[-1] for l in possible_media_links], possible_media_links))
            # For videos it gives us a preview, and there's other possible junk... so prioritize videos then images then fallback on whatever we can...
            for suf in ['mp4', 'webm', 'gif', 'png', 'jpg', 'jpeg']:
                if suf in suffix_mapto_link:
                    return suffix_mapto_link[suf]
            return possible_media_links[0]
    return url


def fix_giphy_url(url):
    if 'gfycat.com' in url:
        # keep just the caPiTALIZed key and return a nice predictable url
        return re.sub('.*gfycat.com/([^-]+)-.*', r'https://thumbs.gfycat.com/\1-mobile.mp4', url)
    return url


def fix_redd_url(url):
    if 'v.redd.it' in url:
        # Unfortunately we can't predict what quality levels are available beforehand
        # Protip from https://www.joshmcarthur.com/til/2019/05/20/httpsvreddit-video-urls.html
        vid_id = re.findall('v.redd.it/([A-Za-z0-9]+)',url)[0]
        dash_playlist = requests.get(f'https://v.redd.it/{vid_id}/DASHPlaylist.mpd')
        available_qs = re.findall(r'DASH_(\d+)\.mp4',dash_playlist.text)
        best_q = sorted(available_qs, key=lambda x: -int(x))[0]
        return f'https://v.redd.it/{vid_id}/DASH_{best_q}.mp4'
    else:
        return url


def fix_url_in_dict(d):
    if d['gfycat']:
        return fix_giphy_url(d['gfycat'])
    else:
        return fix_redd_url(fix_imgur_url(d['url']))


def query_reddit_api(config, limit=10):
    # Try really hard to get reddit api results. Sometimes the reddit API gives back empty jsons.
    for attempt in range(config['MAX_REDDIT_API_ATTEMPTS']):
        r = requests.get(f"https://www.reddit.com/r/aww/top.json?limit={limit}", headers={'User-Agent': 'linux:top-cat:v0.2.0'})
        j = r.json()
        if j.get("data") is not None:
            if config['VERBOSE']:
                print( "Succesfully queried the reddit api after", attempt+1, "attempts" , file=sys.stderr)
            break
        else:
            if config['VERBOSE']:
                print( "Attempt", attempt, "at reddit api call failed. Trying again..." , file=sys.stderr)
        sleep(0.1)
    assert j.get("data") is not None, "Can't seem to query the reddit api! (Maybe try again later?)"
    # We've got the data for sure now.
    nice_jsons = pyjq.all('.data.children[].data|{title, url, orig_url: .url, gfycat: .media.oembed.thumbnail_url}', j)
    # fix imgur and giphy urls:
    nice_jsons = [ {**d, 'url':fix_url_in_dict(d)} for d in nice_jsons ]
    return nice_jsons


def add_image_content_to_post_d(post, temp_dir):
    " Add the image data to our post dictionary. Don't bother if it's already there. "
    if post.get('media_file') is None:
        #FIXME: Make this write to a tempfile instead
        temp_fname = f"{temp_dir.name}/{''.join(random.choice(string.ascii_lowercase) for i in range(20))}.{post['url'].split('.')[-1]}"
        post['media_file'] = temp_fname
        open(temp_fname,'wb').write(requests.get(post['url'], stream=True).content)
        post['media_hash'] = hashlib.sha1(open(temp_fname,'rb').read()).hexdigest()


def add_labels_for_image_to_post_d(post, labelling_function):
    frames_in_video = cast_to_pil_imgs(
                        extract_frames_from_im_or_video(post['media_file'])
                    )
    proportion_label_in_post = labelling_function(frames_in_video)

    # Delete labels below threshold
    for label in list(proportion_label_in_post.keys()):
        if proportion_label_in_post[label] < SCORE_CUTOFF:
            # print(f'deleting {label} from consideration {proportion_label_in_post[label]} < {SCORE_CUTOFF}', file=sys.stderr)
            del proportion_label_in_post[label]

    # Add labels and scores to posts
    post['labels'] = list(proportion_label_in_post.keys())
    post['scores'] = list(proportion_label_in_post.values())


def extract_frames_from_im_or_video(media_file):
    mime_t = mimetypes.MimeTypes().guess_type(media_file)[0]
    if mime_t.split('/')[0] == 'video' or mime_t == 'image/gif':
        # Modified from https://answers.opencv.org/question/62029/extract-a-frame-every-second-in-python/
        to_ret = []
        cap = cv2.VideoCapture(media_file)
        frame_rate = cap.get(cv2.CAP_PROP_FPS)
        frames_in_video = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        seconds_in_video = frames_in_video/frame_rate
        if seconds_in_video > MAX_IMS_PER_VIDEO:
            frames_to_grab = np.linspace(0, frames_in_video-1, num=MAX_IMS_PER_VIDEO, dtype=int)
        else:
            frames_to_grab = [int(f) for f in np.arange(0,frames_in_video,frame_rate)]
        while(cap.isOpened()):
            frame_id = cap.get(cv2.CAP_PROP_POS_FRAMES)
            got_a_frame, frame = cap.read()
            if not got_a_frame:
                break
            if int(frame_id) in frames_to_grab:
                to_ret.append(frame)
    #             filename = "/Users/nim/git/top_cat/debug/image_" +  str(int(frame_id)) + ".jpg"
    #             cv2.imwrite(filename, frame)
        cap.release()
        return to_ret
    else:
        return [Image.open(media_file)]



def cast_to_pil_imgs(img_or_vid):
    if issubclass(type(img_or_vid),Image.Image):
        return [img_or_vid]
    elif type(img_or_vid) == np.ndarray:
        return [Image.fromarray(cv2.cvtColor(img_or_vid, cv2.COLOR_BGR2RGB))]
    elif type(img_or_vid) == list and len(img_or_vid) == 0:
        print('# WARNING: Blank list passed', file=sys.stderr)
        return img_or_vid
    elif type(img_or_vid) == list and issubclass(type(img_or_vid[0]),Image.Image):
        return img_or_vid
    elif type(img_or_vid) == list and type(img_or_vid[0]) == np.ndarray:
        return [ Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) for frame in img_or_vid ]
    else:
        print("# WARNING: wtf is the image?", img_or_vid, type(img_or_vid), type(img_or_vid[0]), file=sys.stderr)
        return img_or_vid



def populate_labels_in_db_for_posts(
              reddit_response_json
            , labelling_function
            , temp_dir
            , db_conn
            , config
        ):
    # Make sure we have the images and labels stashed for any potentially new posts
    # Usually we just skip adding labels for a post since it's probably been in the top N for a few hours already and had many chances to be labelled already
    for post_i, post in enumerate(reddit_response_json):
        image_found = QUERIES.get_top_post_given_url(db_conn, **post)
        if not image_found:
            # Did not find the url, must be a new post. (or maybe a repost...)
            add_image_content_to_post_d(post,temp_dir)
            add_labels_for_image_to_post_d(post,labelling_function)

            #Check if we already have the file in the db
            QUERIES.record_post(db_conn, **post)
            db_conn.commit()
            post_id = QUERIES.get_top_post_given_url(db_conn, **post)[0]
            post['post_id'] = post_id

            # Print out each label and label's score. Also store each result in the db.)
            if config['VERBOSE']:
                print("Labels for", file=sys.stderr)
                print(post['title'], ':', post['url'], file=sys.stderr)
            for label, score in zip(post['labels'],post['scores']):
                # Sometimes a few pixels get ridiculous labels... 10% of the pixels having a label seems like a decent cutoff...
                if config['VERBOSE']:
                    print('    ',label,'=',score, file=sys.stderr)
                if label != 'background':
                    QUERIES.record_post_label(db_conn, post_id=post_id, label=label, score=score)
                    db_conn.commit()
        else:
            post['post_id'] = image_found[0]
            post['media_hash'] = image_found[1]
            # Fetch labels from db
            fetched_labels = QUERIES.get_labels_and_scores_for_post(db_conn, **post)
            if fetched_labels:
                post['labels'], post['scores'] = zip(*fetched_labels)
            else:
                post['labels'] = ['background']
                post['scores'] = [1.0]


def maybe_repost_to_slack(post, label, top_cat_config):
    if top_cat_config['POST_TO_SLACK_TF']:
        label_map_to_channel = dict(zip(top_cat_config['LABELS_TO_SEARCH_FOR'],
                                        top_cat_config['SLACK_CHANNELS']))
        slack_payload = {
            "token": top_cat_config['SLACK_API_TOKEN'],
            "channel": label_map_to_channel[label],
            "text": f"Top {label} on /r/aww\n{post['url']}",
            "username": f"Top{label.title()}",
            "as_user": f"Top{label.title()}",
            "attachments": json.dumps([
                    {
                        "fallback": f"Top {label} on /r/aww\n{post['url']}",
                        "title": post['title'],
                        # Currently this only works for images and gifs... :(
#                        "image_url": post['url']
                    }
                ])
        }
        requests.get('https://slack.com/api/chat.postMessage', params=slack_payload)
        if top_cat_config['VERBOSE']:
            print('Posted to slack')

# # CURRENTLY BROKEN. Facebook got rid of my access and won't give me a new one...
# def repost_to_facebook(post, label, top_cat_config):
#     if top_cat_config['POST_TO_FB_TF']:
#         fb_api = facebook.GraphAPI(top_cat_config['FB_PAGE_ACCESS_TOKEN'])
#         attachment =  {
#             'name': 'top_cat',
#             'link': img,
#             'picture': img
#         }
#         status = fb_api.put_wall_post(message=unicode(links_map_to_title[img]), attachment=attachment)


def update_config_with_args(config, args):
    # { 'DB_FILE': '--db-file' ... }
    args_keys_to_conig_keys = dict(zip(args.keys(),  [ a.strip('-').replace('-','_').upper() for a in args.keys()]))
    config.update([ (args_keys_to_conig_keys[argk], args[argk]) for argk in args.keys() if args[argk] is not None ])


def maybe_repost_to_social_media(reddit_response_json, top_cat_config, db_conn):
    # We're ready to figure out if the post has climbed up the ranks and become a top post
    # Only consider the first post... maybe do something fancier later.
    top_post = reddit_response_json[0]
    for label_to_search_for in top_cat_config['LABELS_TO_SEARCH_FOR']:
        # Iterate down the list of reddit posts and see if there's a label_to_search_for for the post.
        if label_to_search_for in top_post['labels']:
            already_reposted = QUERIES.did_we_already_repost(db_conn, post_id=top_post['post_id'], label=label_to_search_for)
            if not already_reposted:
                maybe_repost_to_slack(top_post,label_to_search_for,top_cat_config)
                # repost_to_facebook(top_post,label_to_search_for,top_cat_config)
                QUERIES.record_the_repost(db_conn, post_id=top_post['post_id'], label=label_to_search_for)
                db_conn.commit()
                print(f'Got a new top {label_to_search_for}: {top_post["title"]} {top_post["url"]}')


def get_labelling_funtion_given_config(config):
    if config['USE_GOOGLE_VISION']:
        from google.cloud import vision
        gvision_client = vision.ImageAnnotatorClient()
        from google_vision_labeler import get_labels_from_frames_gvision
        def labelling_funtion_gvision(frames):
            return get_labels_from_frames_gvision(gvision_client, frames)
        return labelling_funtion_gvision
    else:
        # Only load tf and the deeplab model now that we've decided we want them
        import tensorflow as tf
        # Turn off useless TF messages
        os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'; tf.compat.v1.logging.set_verbosity(tf.compat.v1.logging.ERROR)
        from deeplab import DeepLabModel, get_labels_from_frames_deeplab
        # Get the vision model ready
        deeplabv3_model_tar = tf.keras.utils.get_file(
            fname=config['DEEPLABV3_FILE_NAME'],
            origin="http://download.tensorflow.org/models/"+config['DEEPLABV3_FILE_NAME'],
            cache_subdir='models')
        model = DeepLabModel(deeplabv3_model_tar)
        def labelling_funtion_deeplabv3(frames):
            return get_labels_from_frames_deeplab(model, frames)
        return labelling_funtion_deeplabv3

def main():
    temp_dir = TemporaryDirectory()

    # Parse args and prepare configuration
    args = docopt(__doc__, version='0.2.0')
    top_cat_config = get_config(config_file_loc=args['--config'])
    update_config_with_args(top_cat_config, args)

    # Connect to the db. Create the sqlite file if necessary.
    db_conn = sqlite3.connect(os.path.expanduser(top_cat_config['DB_FILE']))
    guarantee_tables_exist(db_conn)

    # Depending on the config, we will prepare wrapper around a tensorflow model (deeplabv3) XOR around the google vision api
    labelling_function = get_labelling_funtion_given_config(top_cat_config)

    # What's new in /r/aww?
    reddit_response_json = query_reddit_api(top_cat_config)

    # Label everything... not really necessary since we could just label
    #   the top_post but nice to have in the db regardless
    populate_labels_in_db_for_posts(
              reddit_response_json=reddit_response_json
            , labelling_function=labelling_function
            , temp_dir=temp_dir
            , db_conn=db_conn
            , config=top_cat_config
        )

    if top_cat_config['VERBOSE']:
        pprint.pp(reddit_response_json)

    maybe_repost_to_social_media(reddit_response_json, top_cat_config, db_conn)



if __name__ == "__main__":
    main()
