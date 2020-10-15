from top_cat import *
import pytest
import sys
import glob

LABELS_TO_CARE_ABOUT = set(['cat', 'dog'])

def get_posts_including_labels_and_correctness(labelling_function):
    media_files = glob.glob('imgs/*/*')
    post_dicts = [{'media_file':i, 'label_to_find':i.split('/')[1]} for i in glob.glob('imgs/*/*')]

    for post in post_dicts:
        add_labels_for_image_to_post_d(post, labelling_function)

    for post in post_dicts:
        # Look for cats and dogs, but make sure we didn't just tack on every label willy nilly
        # Example: Want to find a cat, passes if we found a cat but no dogs
        # Example: label to find is 'galaxy' but we don't care about that for top cat/dog,
        #    so it's ok as long we don't find cats or dogs too.
        post['correct_label'] = (
                    ( post['label_to_find'] in post['labels']   # want a cat, found one
                        or
                      post['label_to_find'] not in LABELS_TO_CARE_ABOUT   # want 'galaxy' which is not a cat or dog
                    )
                    # Also make sure we didn't get dogs when we were looking for cats
                and not ( LABELS_TO_CARE_ABOUT - set([post['label_to_find']]) ).intersection(post['labels'])
            )
        if not post['correct_label']:
            print(f'#WARNING: Wrong label for {post["media_file"]}, expected {post["label_to_find"]}.', file=sys.stderr)
    return post_dicts

def test_gvision():
    config = get_config()
    config['MODEL_TO_USE'] = 'gvision_labeler'
    labelling_function = get_labelling_funtion(config)

    post_dicts = get_posts_including_labels_and_correctness(labelling_function)
    assert all([ p['correct_label'] for p in post_dicts])

def test_deeplab():
    config = get_config()
    config['MODEL_TO_USE'] = 'deeplab'
    labelling_function = get_labelling_funtion(config)

    post_dicts = get_posts_including_labels_and_correctness(labelling_function)
    assert all([ p['correct_label'] for p in post_dicts])

