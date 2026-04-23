<?php
/**
 * Nakama Publisher: register nakama_draft_id post meta as REST-searchable.
 *
 * Without this, GET /wp/v2/posts?meta_key=nakama_draft_id&meta_value=...
 * silently returns empty — the idempotency dedup chain degrades to single-layer.
 *
 * Paste this block into your active theme's functions.php or a mu-plugin.
 * (ADR-005b §2 / §2.1)
 */
add_action('init', function () {
    register_post_meta('post', 'nakama_draft_id', [
        'show_in_rest'  => true,
        'single'        => true,
        'type'          => 'string',
        'auth_callback' => function () {
            return current_user_can('edit_posts');
        },
    ]);
});
