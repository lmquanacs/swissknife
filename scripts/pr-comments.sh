#!/bin/sh
# pr-comments.sh — export every comment on a GitHub pull request except the author's.
#
# Usage: pr-comments.sh <pr-url> [-o FILE] [--exclude LOGIN]... [--no-bots]
#
# Takes a pull request link, asks the GitHub API for all three kinds of comment a
# PR can carry, drops everything written by the PR's own author, and writes the
# rest to one JSON file sorted oldest-first.
#
#   issue_comment   the top-level conversation tab
#   review_comment  inline comments on the diff (carry path, line, diff_hunk)
#   review          the summary body of a review (approvals with no body are not
#                   comments, so they are dropped)
#
# Options
#   -o, --output FILE   where to write (default: pr-<owner>-<repo>-<number>-comments.json)
#                       use "-" to write to stdout instead of a file
#       --exclude LOGIN also drop this login; repeat for more than one
#       --no-bots       also drop bot accounts ([bot] logins and user type Bot)
#   -h, --help          print this help
#
# Examples
#   pr-comments.sh https://github.com/cli/cli/pull/9000
#   pr-comments.sh https://github.com/cli/cli/pull/9000 -o review-feedback.json
#   pr-comments.sh https://github.com/cli/cli/pull/9000 --no-bots --exclude dependabot
#   pr-comments.sh https://github.com/cli/cli/pull/9000 -o - | jq -r '.comments[].body'
#
# Output shape
#   { "pull_request": { url, host, owner, repo, number, author, title, state },
#     "generated_at": "<ISO-8601 UTC>",
#     "excluded_logins": [ ... ],
#     "count": <n>,
#     "comments": [ { id, type, author, author_type, author_association,
#                     created_at, updated_at, url, body, path, line, start_line,
#                     side, commit_id, diff_hunk, in_reply_to_id, review_id,
#                     review_state } ] }
#
# Requires: gh (authenticated — see `gh auth login`) and jq.
# Enterprise hosts work: the host in the link is used as GH_HOST.

set -eu

PROG=$(basename "$0")

usage() {
	sed -n '2,/^# Requires:/p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
	printf '%s: %s\n' "$PROG" "$1" >&2
	exit 1
}

PR_URL=""
OUTPUT=""
NO_BOTS=0
EXCLUDES=""

while [ $# -gt 0 ]; do
	case "$1" in
	-h | --help)
		usage
		exit 0
		;;
	-o | --output)
		[ $# -ge 2 ] || die "$1 needs a value"
		OUTPUT=$2
		shift 2
		;;
	--output=*)
		OUTPUT=${1#--output=}
		shift
		;;
	--exclude)
		[ $# -ge 2 ] || die "$1 needs a value"
		EXCLUDES="$EXCLUDES$2
"
		shift 2
		;;
	--exclude=*)
		EXCLUDES="$EXCLUDES${1#--exclude=}
"
		shift
		;;
	--no-bots)
		NO_BOTS=1
		shift
		;;
	-*)
		die "unknown option: $1 (try --help)"
		;;
	*)
		[ -z "$PR_URL" ] || die "expected one pull request URL, got a second: $1"
		PR_URL=$1
		shift
		;;
	esac
done

[ -n "$PR_URL" ] || {
	usage >&2
	exit 2
}

command -v gh >/dev/null 2>&1 || die "gh is not installed — see https://cli.github.com"
command -v jq >/dev/null 2>&1 || die "jq is not installed"

# --- parse the link ---------------------------------------------------------
# https://github.com/OWNER/REPO/pull/NUMBER[/files][?query][#fragment]
rest=${PR_URL#*://}
[ "$rest" != "$PR_URL" ] || rest=$PR_URL # tolerate a link with no scheme
rest=${rest%%\#*}
rest=${rest%%\?*}

HOST=${rest%%/*}
rest=${rest#*/}
OWNER=${rest%%/*}
rest=${rest#*/}
REPO=${rest%%/*}
rest=${rest#*/}
KIND=${rest%%/*}
rest=${rest#*/}
NUMBER=${rest%%/*}

case "$KIND" in
pull | pulls) ;;
*) die "not a pull request link (expected .../pull/<number>): $PR_URL" ;;
esac

case "$HOST$OWNER$REPO" in
*/* | "") die "could not parse owner/repo from: $PR_URL" ;;
esac

case "$NUMBER" in
'' | *[!0-9]*) die "could not parse a pull request number from: $PR_URL" ;;
esac

[ "$HOST" = "github.com" ] || {
	GH_HOST=$HOST
	export GH_HOST
}

[ -n "$OUTPUT" ] || OUTPUT="pr-$OWNER-$REPO-$NUMBER-comments.json"

# --- fetch ------------------------------------------------------------------
TMPDIR_WORK=$(mktemp -d "${TMPDIR:-/tmp}/pr-comments.XXXXXX")
trap 'rm -rf "$TMPDIR_WORK"' EXIT INT TERM HUP

PR_JSON=$TMPDIR_WORK/pr.json
RAW=$TMPDIR_WORK/raw.jsonl
: >"$RAW"

gh api "repos/$OWNER/$REPO/pulls/$NUMBER" >"$PR_JSON" ||
	die "could not read $OWNER/$REPO#$NUMBER — is the link right and are you authenticated? (gh auth status)"

AUTHOR=$(jq -r '.user.login // ""' "$PR_JSON")
[ -n "$AUTHOR" ] || die "the pull request has no author login — cannot exclude it"

# Each endpoint returns an array per page; --jq '.[]' flattens the pages into one
# JSONL stream, which jq -s can then collect without --slurp's array-of-arrays.
fetch() {
	endpoint=$1
	kind=$2
	gh api --paginate "repos/$OWNER/$REPO/$endpoint?per_page=100" --jq '.[]' |
		jq -c --arg t "$kind" '. + {__type: $t}' >>"$RAW"
}

fetch "issues/$NUMBER/comments" issue_comment
fetch "pulls/$NUMBER/comments" review_comment
fetch "pulls/$NUMBER/reviews" review

# --- shape ------------------------------------------------------------------
EXCLUDED_JSON=$(printf '%s%s\n' "$EXCLUDES" "$AUTHOR" |
	jq -R . | jq -s 'map(select(length > 0) | ascii_downcase) | unique')

RESULT=$TMPDIR_WORK/result.json
jq -s \
	--argjson excluded "$EXCLUDED_JSON" \
	--argjson pr "$(jq -c '{title, state, author: .user.login, html_url}' "$PR_JSON")" \
	--argjson nobots "$NO_BOTS" \
	--arg url "$PR_URL" \
	--arg host "$HOST" \
	--arg owner "$OWNER" \
	--arg repo "$REPO" \
	--argjson number "$NUMBER" \
	--arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
	'
	def normalize:
		{
			id:                 .id,
			type:               .__type,
			author:             (.user.login // null),
			author_type:        (.user.type // null),
			author_association: (.author_association // null),
			created_at:         (.created_at // .submitted_at // null),
			updated_at:         (.updated_at // .submitted_at // null),
			url:                (.html_url // null),
			body:               (.body // ""),
			path:               (.path // null),
			line:               (.line // .original_line // null),
			start_line:         (.start_line // .original_start_line // null),
			side:               (.side // null),
			commit_id:          (.commit_id // null),
			diff_hunk:          (.diff_hunk // null),
			in_reply_to_id:     (.in_reply_to_id // null),
			review_id:          (.pull_request_review_id // (if .__type == "review" then .id else null end)),
			review_state:       (if .__type == "review" then .state else null end)
		};
	def is_bot: (.author_type == "Bot") or ((.author // "") | endswith("[bot]"));

	[.[] | normalize]
	| map(select(.author != null))
	| map(select(.type != "review" or (.body | length) > 0))
	| map(select((.author | ascii_downcase) as $a | ($excluded | index($a)) | not))
	| (if $nobots == 1 then map(select(is_bot | not)) else . end)
	| sort_by(.created_at // "")
	| {
		pull_request: {
			url: $url, host: $host, owner: $owner, repo: $repo, number: $number,
			author: $pr.author, title: $pr.title, state: $pr.state
		},
		generated_at: $now,
		excluded_logins: $excluded,
		count: length,
		comments: .
	}
	' "$RAW" >"$RESULT"

if [ "$OUTPUT" = "-" ]; then
	cat "$RESULT"
else
	cat "$RESULT" >"$OUTPUT"
	printf '%s: wrote %s comment(s) from %s to %s\n' \
		"$PROG" "$(jq -r '.count' "$RESULT")" "$OWNER/$REPO#$NUMBER" "$OUTPUT" >&2
fi
