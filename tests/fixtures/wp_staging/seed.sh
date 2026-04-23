#!/usr/bin/env bash
# Seed script for WP staging environment.
# Run inside the `wp` container: docker compose exec wp bash /seed.sh
#
# What this does:
#   1. Installs WP CLI
#   2. Installs + activates SEOPress 9.4.1
#   3. Creates nakama_publisher role (custom Editor-derived role)
#   4. Creates nakama_publisher WP user + generates application password
#   5. Registers nakama_draft_id post meta (REST-searchable)
#   6. Creates 20 seed posts across 3 categories
#
# Output: prints the generated application password at the end.
set -euo pipefail

WP_CLI=/usr/local/bin/wp
WP_CORE_DIR=/var/www/html

# ── Install WP-CLI ──────────────────────────────────────────────────────────
if [ ! -f "$WP_CLI" ]; then
  curl -sSo "$WP_CLI" https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
  chmod +x "$WP_CLI"
fi

# Allow running as root (Docker default)
WP="wp --allow-root --path=$WP_CORE_DIR"

# ── Install + activate WP (if not already done) ─────────────────────────────
if ! $WP core is-installed 2>/dev/null; then
  $WP core install \
    --url="http://localhost:8888" \
    --title="Nakama WP Staging" \
    --admin_user="admin" \
    --admin_password="nakama_test_pw" \
    --admin_email="admin@nakama.test" \
    --skip-email
fi

# ── Install SEOPress 9.4.1 ──────────────────────────────────────────────────
if ! $WP plugin is-active seopress 2>/dev/null; then
  $WP plugin install seopress --version=9.4.1 --activate || \
    $WP plugin install seopress --activate  # fallback: latest
fi

# ── Create nakama_publisher role ────────────────────────────────────────────
$WP eval '
$role = get_role("nakama_publisher");
if (!$role) {
  $editor = get_role("editor");
  add_role(
    "nakama_publisher",
    "Nakama Publisher",
    $editor->capabilities
  );
  // Explicitly remove dangerous capabilities
  $role = get_role("nakama_publisher");
  $role->remove_cap("edit_users");
  $role->remove_cap("delete_users");
  $role->remove_cap("promote_users");
  $role->remove_cap("manage_options");
  $role->remove_cap("install_plugins");
  $role->remove_cap("activate_plugins");
  $role->remove_cap("update_plugins");
  $role->remove_cap("install_themes");
  $role->remove_cap("update_themes");
  $role->remove_cap("edit_theme_options");
  echo "nakama_publisher role created\n";
} else {
  echo "nakama_publisher role already exists\n";
}
'

# ── Create nakama_publisher user ────────────────────────────────────────────
if ! $WP user get nakama_publisher --field=ID 2>/dev/null; then
  $WP user create \
    nakama_publisher \
    nakama_publisher@nakama.test \
    --role=nakama_publisher \
    --user_pass=temp_pass_123
fi

# ── Generate application password ────────────────────────────────────────────
echo ""
echo "=== Generating application password for nakama_publisher ==="
APP_PASS=$($WP eval '
$user = get_user_by("login", "nakama_publisher");
$uuid = wp_generate_uuid4();
$raw_app_pass = "TestAppPass123!";
$hashed = WP_Application_Passwords::create_new_application_password(
  $user->ID,
  ["name" => "nakama-staging", "app_id" => $uuid]
);
if (is_array($hashed)) {
  echo $hashed[0];
}
')
echo "Application password: $APP_PASS"
echo "(Set WP_SHOSHO_APP_PASSWORD=$APP_PASS in your .env.test)"

# ── Register nakama_draft_id post meta (REST-searchable) ─────────────────────
# Append to functions.php if not already present
SNIPPET=$(cat /functions-snippet.php)
if ! grep -q "nakama_draft_id" "$WP_CORE_DIR/wp-content/themes/$(wp theme list --status=active --field=name --allow-root)/functions.php" 2>/dev/null; then
  echo "$SNIPPET" >> "$WP_CORE_DIR/wp-content/themes/$($WP theme list --status=active --field=name)/functions.php"
  echo "Registered nakama_draft_id post meta"
fi

# ── Create categories ────────────────────────────────────────────────────────
for SLUG in sleep-science nutrition-science neuroscience; do
  $WP term create category "$SLUG" --slug="$SLUG" 2>/dev/null || true
done

# ── Create 20 seed posts ─────────────────────────────────────────────────────
echo ""
echo "=== Creating 20 seed posts ==="
CATEGORIES="sleep-science nutrition-science neuroscience"
i=0
for CAT in $CATEGORIES $CATEGORIES $CATEGORIES $CATEGORIES $CATEGORIES $CATEGORIES $CATEGORIES; do
  i=$((i+1))
  if [ $i -gt 20 ]; then break; fi
  CAT_ID=$($WP term get category "$CAT" --field=term_id 2>/dev/null || echo 1)
  $WP post create \
    --post_title="Seed Post $i — $CAT" \
    --post_content="<p>This is seed post $i for category $CAT. For testing purposes only.</p>" \
    --post_status=publish \
    --post_category="$CAT_ID" \
    --post_author=1 \
    2>/dev/null || true
  echo "  Created post $i ($CAT)"
done

echo ""
echo "=== Staging setup complete ==="
echo "WP Admin:         http://localhost:8888/wp-admin"
echo "Admin user:       admin / nakama_test_pw"
echo "Publisher user:   nakama_publisher"
echo "REST base:        http://localhost:8888/wp-json/wp/v2/"
