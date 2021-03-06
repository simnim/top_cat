import hashlib
import sqlite3
from tempfile import NamedTemporaryFile, TemporaryDirectory

import cv2
import pytest
import toml
from PIL import Image

from top_cat import (
    QUERIES,
    THIS_SCRIPT_DIR,
    add_image_content_to_post_d,
    add_labels_for_image_to_post_d,
    cast_to_pil_imgs,
    extract_frames_from_im_or_video,
    fix_giphy_url,
    fix_imgur_url,
    fix_redd_url,
    get_config,
    get_labelling_funtion,
    get_sha1_lowmemuse,
    guarantee_tables_exist,
    maybe_repost_to_social_media,
    populate_labels_in_db_for_posts,
    query_reddit_api,
    update_config_with_args,
)


# get_config
def test_get_config_just_defaults():
    config = get_config("/dev/null")
    assert config == toml.load(THIS_SCRIPT_DIR + "/default_config.toml")


def test_get_config_one_opt():
    tempf = NamedTemporaryFile()
    open(tempf.name, "w").write('DB_FILE = "derp"\n')
    config = get_config(tempf.name)
    assert config.get("DB_FILE") == "derp"


def test_get_config_bad_opt():
    tempf = NamedTemporaryFile()
    open(tempf.name, "w").write('DB_FILEZ = "derp"\n')
    with pytest.raises(AssertionError) as excinfo:
        get_config(tempf.name)
    excinfo.match(r"Maybe you meant DB_FILE")


def test_guarantee_tables_exist():
    tempf = NamedTemporaryFile()
    db_conn = sqlite3.connect(tempf.name)
    guarantee_tables_exist(db_conn)
    db_cur = db_conn.cursor()
    db_cur.execute("select type, name from sqlite_master;")
    db_objects = frozenset(db_cur.fetchall())
    assert db_objects == (
        frozenset(
            {
                ("index", "post_label_post_id_index"),
                ("index", "media_url_index"),
                ("index", "top_post_post_id_index"),
                ("table", "post"),
                ("table", "post_label"),
                ("table", "top_post"),
            }
        )
    )


@pytest.mark.net
def test_fix_imgur_url_video():
    assert (
        fix_imgur_url("https://imgur.com/zH3iA75") == "https://i.imgur.com/zH3iA75.mp4"
    )


@pytest.mark.net
def test_fix_imgur_pic():
    assert fix_imgur_url("https://imgur.com/2cfU6dh") in [
        "https://i.imgur.com/2cfU6dh.jpg",
        "https://i.imgur.com/2cfU6dh.jpeg",
    ]


@pytest.mark.net
def test_fix_imgur_gifv():
    assert (
        fix_imgur_url("https://imgur.com/zH3iA75") == "https://i.imgur.com/zH3iA75.mp4"
    )


def test_fix_giphy_url():
    assert (
        fix_giphy_url(
            "https://thumbs.gfycat.com/HorribleFakeEmperorshrimp-size_restricted.gif"
        )
        == "https://thumbs.gfycat.com/HorribleFakeEmperorshrimp-mobile.mp4"
    )


@pytest.mark.net
def test_fix_redd_url():
    assert (
        fix_redd_url("https://v.redd.it/midbybt5fmh51")
        == "https://v.redd.it/midbybt5fmh51/DASH_720.mp4"
    )


# def test_fix_url_in_dict():
#     pass


@pytest.mark.net
def test_query_reddit_api():
    reddit_response_json = query_reddit_api(
        {"MAX_REDDIT_API_ATTEMPTS": 10, "VERBOSE": False, "MAX_POSTS_TO_PROCESS": 10}
    )
    assert (
        type(reddit_response_json) == list
        and len(reddit_response_json) >= 1
        and reddit_response_json[0].keys()
        == frozenset(["title", "url", "orig_url", "gfycat"])
    )


@pytest.mark.net
def test_add_image_content_to_post_d():
    temp_dir = TemporaryDirectory()
    post = {
        "title": "this is a test",
        "url": "https://i.redd.it/ld0ct5djqkh51.jpg",
        "orig_url": "https://i.redd.it/ld0ct5djqkh51.jpg",
        "gfycat": None,
    }
    add_image_content_to_post_d(post, temp_dir)
    assert (
        post.get("media_file") is not None
        and post.get("media_hash") == "c241691625515c29b02a4a66f3c947ba71566168"
    )


def test_get_sha1_lowmemuse():
    assert (
        get_sha1_lowmemuse(THIS_SCRIPT_DIR + "/imgs/dog/ld0ct5djqkh51.jpg")
        == "c241691625515c29b02a4a66f3c947ba71566168"
    )


def test_add_labels_for_image_to_post_d():
    post = {
        "title": "this is a test",
        "url": "https://i.redd.it/ld0ct5djqkh51.jpg",
        "orig_url": "https://i.redd.it/ld0ct5djqkh51.jpg",
        "gfycat": None,
        "media_file": THIS_SCRIPT_DIR + "/imgs/dog/ld0ct5djqkh51.jpg",
        "media_hash": "c241691625515c29b02a4a66f3c947ba71566168",
    }

    def labelling_function(frames):
        return {"dog": 0.7}

    add_labels_for_image_to_post_d(post, labelling_function, {"MAX_IMS_PER_VIDEO": 10})
    assert (
        post.get("labels") is not None
        and post.get("labels") == ["dog"]
        and post.get("scores") is not None
        and post.get("scores") == [0.7]
    )


def test_extract_frames_from_im_or_video():
    # Test currently relies on MAX_IMS_PER_VIDEO == 10
    frames = extract_frames_from_im_or_video(
        THIS_SCRIPT_DIR + "/imgs/cat/wzkv43qxa1c51.mp4", {"MAX_IMS_PER_VIDEO": 10}
    )
    frame_hashes = tuple(hashlib.sha1(bytes(f)).hexdigest() for f in frames)
    assert frame_hashes == (
        "5fb877585c64b2b7233a8305f2b9d43243f8a82b",
        "185ee4dbf2d611f9779750fc29ee55bc54378f70",
        "1d05cfea3623d250a23313c04f42dbe1eb68efcf",
        "1f3a086a3c27f9b729fde40f8d2803e2121c42f8",
        "4980fca358de13b27bb496faafba4428da551d61",
        "ed0aae7eb4ab2e24312f0a64183bcc2ddcdf285f",
        "131164e443614a10585ae161cce398ea92d49fa0",
        "8447afe567fd94c1feb9a219716e2eeecd2d0a45",
        "0c54a37778afd94c1233dc99132dcca1c936e5ca",
        "40c42bfd636e74031f911db7f28a9d52371d65cd",
    )


def test_cast_to_pil_imgs_from_pil():
    pil_im = Image.open(THIS_SCRIPT_DIR + "/imgs/cat/cat_with_a_hat.jpg")
    assert [pil_im] == cast_to_pil_imgs(pil_im) and [pil_im] == cast_to_pil_imgs(
        cast_to_pil_imgs(pil_im)
    )


def test_cast_to_pil_imgs_from_cv2():
    pil_im = cv2.imread(THIS_SCRIPT_DIR + "/imgs/cat/cat_with_a_hat.jpg")
    pil_imgs = cast_to_pil_imgs(pil_im)
    assert (
        type(pil_imgs) == list
        and type(pil_imgs[0]) == Image.Image
        and pil_imgs == cast_to_pil_imgs(pil_imgs)
    )


def test_populate_labels_in_db_for_posts():
    # Set up variables to pass
    reddit_response_json = [
        {
            "title": "this is a test",
            "url": "https://i.redd.it/ld0ct5djqkh51.jpg",
            "orig_url": "https://i.redd.it/ld0ct5djqkh51.jpg",
            "gfycat": None,
            "media_file": THIS_SCRIPT_DIR + "/imgs/dog/ld0ct5djqkh51.jpg",
            "media_hash": "c241691625515c29b02a4a66f3c947ba71566168",
        }
    ]

    def labelling_function(frames):
        return {"dog": 0.7}

    temp_dir = TemporaryDirectory()
    tempf = NamedTemporaryFile()
    db_conn = sqlite3.connect(tempf.name)
    config = {"VERBOSE": False, "MODEL_TO_USE": "test"}
    # set up the tables in the db
    guarantee_tables_exist(db_conn)
    # Run the function
    populate_labels_in_db_for_posts(
        reddit_response_json, labelling_function, temp_dir, db_conn, config
    )
    # check that the labels found their way into the db
    labels_in_db = QUERIES.get_labels_and_scores_for_post(db_conn, 1)
    assert labels_in_db == [("dog", 0.7)]


# # Yeah... I don't want to spam my channels... unfortunately I'll have to test this manually...
# def test_repost_to_slack():
#     pass


# # We have to make sure POST_TO_SLACK_TF to False so we don't spam any channels!
def test_maybe_repost_to_social_media():
    config = {"LABELS_TO_SEARCH_FOR": ["dog"], "POST_TO_SLACK_TF": False}
    reddit_response_json = [
        {
            "title": "this is a test",
            "url": "https://i.redd.it/ld0ct5djqkh51.jpg",
            "orig_url": "https://i.redd.it/ld0ct5djqkh51.jpg",
            "gfycat": None,
            "media_file": THIS_SCRIPT_DIR + "/imgs/ld0ct5djqkh51.jpg",
            "media_hash": "c241691625515c29b02a4a66f3c947ba71566168",
            "post_id": 1,
            "labels": ["dog"],
            "scores": [0.7],
        }
    ]
    tempf = NamedTemporaryFile()
    db_conn = sqlite3.connect(tempf.name)
    guarantee_tables_exist(db_conn)
    # because top_post has a constraint: FOREIGN KEY(post_id) REFERENCES post(post_id)
    QUERIES.record_post(
        db_conn,
        url="https://i.redd.it/ld0ct5djqkh51.jpg",
        media_hash="c241691625515c29b02a4a66f3c947ba71566168",
        title="this is a test",
    )
    maybe_repost_to_social_media(reddit_response_json, config, db_conn)
    # Now double check we added a row to top_post;
    assert QUERIES.did_we_already_repost(db_conn, 1, "dog") == (1, "dog")


def test_update_config_with_args():
    config = {"DB_FILE": "a_file"}
    args = {"--db-file": "b_file"}
    update_config_with_args(config, args)
    assert config["DB_FILE"] == "b_file"


@pytest.mark.net
@pytest.mark.slow
def test_get_labelling_funtion():
    base_config = get_config("/dev/null")
    labelling_func_deeplab = get_labelling_funtion(base_config)
    base_config["MODEL_TO_USE"] = "gvision_labeler"
    labelling_func_gvision = get_labelling_funtion(base_config)
    # Make sure we returned the deeplabv3 model by default
    assert "labelling_funtion_deeplabv3" in str(
        labelling_func_deeplab
    ) and "labelling_funtion_gvision" in str(labelling_func_gvision)
