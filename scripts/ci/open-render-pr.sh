#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# Open or refresh the render-only pull request (ADR-3295, ADR-3373).
#
# Run by .github/workflows/docs-render-pr.yml after
# `check_render_on_master.py --open-after-hours` decided a render is owed long
# enough. Renders at the checked-out master tip, commits the rendered paths only
# as the release-bot App with a matching sign-off, force-pushes RENDER_BRANCH,
# opens the pull request when none is open and arms auto-merge, so the forge
# merges it once every required check is green and it is up to date.
#
# An open pull request on RENDER_BRANCH is only considered this workflow's PR
# when it is same-repository and authored by the App (app/${APP_SLUG} or
# ${APP_SLUG}[bot]), ignoring foreign or fork branches of the same name.
#
# An unqueued branch based on the master tip only needs auto-merge armed. A
# queued pull request based on that tip stays queued. If master has moved, compare
# its fresh render with master first. If master is current, dequeue and close
# the stale pull request. Otherwise, leave matching queued files in place or
# dequeue and confirm removal before pushing and arming auto-merge again. The
# queue re-read before the push narrows but does not close the window between
# that read and the push. A failed queue read stops the run. If interrupted after
# dequeue and before push, the next run refreshes the unqueued branch. With
# RENDER_DUE=false (master's render is current) an open render pull request of
# this workflow is closed instead.
# Inputs (environment): GH_TOKEN (App installation token), APP_SLUG,
# GITHUB_REPOSITORY, RENDER_BRANCH, RENDER_DUE, RUNNER_TEMP, OWED_SINCE,
# RENDER_DEQUEUE_POLLS (default 12), RENDER_DEQUEUE_SLEEP_S (default 5).
set -euo pipefail

: "${GH_TOKEN:?}" "${APP_SLUG:?}" "${GITHUB_REPOSITORY:?}" "${RENDER_BRANCH:?}"
: "${RENDER_DUE:?}" "${RUNNER_TEMP:?}" "${OWED_SINCE:=}"
rendered=(CHANGELOG.md docs/adr/README.md docs/adr/titles.md docs/research/titles.md
  docs/adr/by-tag docs/rebase-notes.md)

find_open_pr() {
  gh pr list --repo "$GITHUB_REPOSITORY" --head "$RENDER_BRANCH" \
    --state open --json number,author,isCrossRepository \
    --jq "[.[] | select((.author.login == \"app/${APP_SLUG}\" or .author.login == \"${APP_SLUG}[bot]\") and .isCrossRepository == false)][0].number // empty"
}

read_queue_state() {
  local pr="$1"
  local owner="${GITHUB_REPOSITORY%%/*}"
  local name="${GITHUB_REPOSITORY#*/}"
  local ans
  if ! ans="$(gh api graphql \
    -f "query=query(\$o:String!,\$n:String!,\$p:Int!){repository(owner:\$o,name:\$n){pullRequest(number:\$p){id isInMergeQueue}}}" \
    -f "o=$owner" -f "n=$name" -F "p=$pr" \
    --jq '.data.repository.pullRequest | [(.isInMergeQueue | tojson), (if (.id | type) == "string" and .id != "" then .id else "" end)] | @tsv')"; then
    echo "open-render-pr.sh: merge-queue GraphQL query failed for pull request #$pr" >&2
    return 1
  fi
  IFS=$'\t' read -r queued pr_id <<<"$ans"
  if [[ "$queued" != true && "$queued" != false || -z "$pr_id" ]]; then
    echo "open-render-pr.sh: merge-queue GraphQL query failed for pull request #$pr (invalid answer)" >&2
    return 1
  fi
}

dequeue_render_pr() {
  local pr="$1" polls="${RENDER_DEQUEUE_POLLS-12}" sleep_s="${RENDER_DEQUEUE_SLEEP_S-5}"
  local max_polls=120 i
  if [[ ! "$polls" =~ ^[1-9][0-9]{0,2}$ ]] || ((polls > max_polls)); then
    echo "open-render-pr.sh: invalid RENDER_DEQUEUE_POLLS for pull request #$pr" >&2
    return 1
  fi
  if [[ ! "$sleep_s" =~ ^[0-9]{1,3}$ ]]; then
    echo "open-render-pr.sh: invalid RENDER_DEQUEUE_SLEEP_S for pull request #$pr" >&2
    return 1
  fi
  if ! gh api graphql \
    -f "query=mutation(\$id:ID!){dequeuePullRequest(input:{id:\$id}){clientMutationId}}" \
    -f "id=$pr_id" >/dev/null; then
    echo "open-render-pr.sh: dequeue failed for pull request #$pr" >&2
    return 1
  fi
  for ((i = 0; i < polls; i++)); do
    if ((i > 0)); then
      sleep "$sleep_s"
    fi
    read_queue_state "$pr"
    if [[ "$queued" == false ]]; then
      return 0
    fi
  done
  echo "open-render-pr.sh: pull request #$pr still queued after $polls polls" >&2
  return 1
}

arm_auto_merge() {
  local pr="$1"
  gh pr merge --repo "$GITHUB_REPOSITORY" --auto --squash "$pr" --body-file "$merge_body"
}

close_render_pr() {
  local pr="$1"
  gh pr close --repo "$GITHUB_REPOSITORY" --delete-branch "$pr" \
    --comment "Master's render is current at ${base:0:12}; this render is no longer owed."
  echo "closed render pull request #$pr: master's render is current"
}

base="$(git rev-parse HEAD)"
open_pr="$(find_open_pr)"
queued=false
if [[ "$RENDER_DUE" != true ]]; then
  if [[ -n "$open_pr" ]]; then
    read_queue_state "$open_pr"
    if [[ "$queued" == true ]]; then
      dequeue_render_pr "$open_pr"
    fi
    close_render_pr "$open_pr"
  fi
  exit 0
fi

parent=""
if [[ -n "$open_pr" ]]; then
  read_queue_state "$open_pr"
  parent="$(gh api "repos/$GITHUB_REPOSITORY/branches/$RENDER_BRANCH" \
    --jq '.commit.parents[0].sha')"
  if [[ "$queued" == true && "$parent" == "$base" ]]; then
    echo "leaving render pull request #$open_pr in the merge queue at ${base:0:12}"
    exit 0
  fi
fi

bot="${APP_SLUG}[bot]"
bot_id="$(gh api "users/$bot" --jq .id)"
email="${bot_id}+${bot}@users.noreply.github.com"
merge_body="$RUNNER_TEMP/render-merge-body.txt"
printf '%s\n\nSigned-off-by: %s <%s>\n' \
  "Render-only commit (ADR-3295, ADR-3373): make docs-render at ${base:0:12}." \
  "$bot" "$email" >"$merge_body"

if [[ "$parent" == "$base" && -n "$open_pr" ]]; then
  arm_auto_merge "$open_pr"
  echo "render pull request #$open_pr is already rendered at ${base:0:12}"
  exit 0
fi

make -s docs-render
render_status="$(git status --porcelain --untracked-files=all -- "${rendered[@]}")"
if [[ -z "$open_pr" && -z "$render_status" ]]; then
  echo "make docs-render changed nothing at ${base:0:12}"
  exit 0
fi
stray="$(git status --porcelain --untracked-files=all -- . "${rendered[@]/#/:!}")"
if [[ -n "$stray" ]]; then
  echo "make docs-render wrote outside the rendered paths:" >&2
  echo "$stray" >&2
  exit 1
fi

if [[ -z "$render_status" ]]; then
  if [[ "$queued" == true ]]; then
    dequeue_render_pr "$open_pr"
  fi
  close_render_pr "$open_pr"
  exit 0
fi

git add -- "${rendered[@]}"
remote_url="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
if [[ "$queued" == true ]]; then
  git fetch -q "$remote_url" "refs/heads/$RENDER_BRANCH"
  if git diff --cached --quiet FETCH_HEAD -- "${rendered[@]}"; then
    echo "queued render pull request #$open_pr matches master at ${base:0:12}"
    exit 0
  elif [[ $? != 1 ]]; then
    echo "open-render-pr.sh: could not compare queued render for pull request #$open_pr" >&2
    exit 1
  fi
  dequeue_render_pr "$open_pr"
fi

msg="$RUNNER_TEMP/render-commit.txt"
printf 'chore(docs): render generated changelog and ADR index\n\n%s\n\nSigned-off-by: %s <%s>\n' \
  "Render-only commit (ADR-3295, ADR-3373): make docs-render at ${base:0:12}." \
  "$bot" "$email" >"$msg"
git switch -q -C "$RENDER_BRANCH"
git -c user.name="$bot" -c user.email="$email" commit -q -F "$msg"
if [[ -n "$open_pr" ]]; then
  read_queue_state "$open_pr"
  if [[ "$queued" == true ]]; then
    echo "open-render-pr.sh: pull request #$open_pr was queued again before the push; not pushing" >&2
    exit 1
  fi
fi
git push -q --force "$remote_url" "HEAD:refs/heads/$RENDER_BRANCH"

if [[ -z "$open_pr" ]]; then
  body="$RUNNER_TEMP/render-body.md"
  {
    echo "Render-only pull request (ADR-3295): \`make docs-render\` output from the fragments on master ${base:0:12}, nothing edited by hand."
    echo
    echo "Opened by the scheduled render workflow (ADR-3373) because master has owed this render since ${OWED_SINCE:0:12}. It is re-rendered when master moves and merges through auto-merge once every required check is green."
    echo
    echo "Reproducer: \`make docs-render-check\` exits 0 on the head; \`python3 scripts/ci/check_render_on_master.py\` reports the render at HEAD current."
    echo
    echo "no state delta: render-only, no bug opened or closed."
  } >"$body"
  gh pr create --repo "$GITHUB_REPOSITORY" --base master --head "$RENDER_BRANCH" \
    --title "chore(docs): render generated changelog and ADR index" --body-file "$body"
  open_pr="$(find_open_pr)"
fi
arm_auto_merge "$open_pr"
echo "render pull request #$open_pr rendered at ${base:0:12}, auto-merge armed"
