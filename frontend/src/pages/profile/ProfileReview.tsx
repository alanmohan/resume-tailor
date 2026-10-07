import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorAlert, PageHeader, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { confirmProfile, updateProfile } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { isProfileReady } from '@/lib/hooks'
import { mutationKeys, queryKeys } from '@/lib/queryKeys'
import type { ConflictResolutionInput, Profile, RecordCategory } from '@/lib/types'
import { ConflictPanel } from './ConflictPanel'
import { ContactCard } from './ContactCard'
import { IndexStatus } from './IndexStatus'
import {
  CATEGORY_SECTIONS,
  draftFromProfile,
  isDirty,
  newRecord,
  toPatchRequest,
  unresolvedConflicts,
  validateDraft,
  type DraftContact,
  type DraftRecord,
  type ProfileDraft,
} from './profileDraft'
import { RecordCard } from './RecordCard'

const numberFormat = new Intl.NumberFormat('en-US')

interface ProfileReviewProps {
  /** The latest profile from the server (refreshed by polling while indexing). */
  profile: Profile
}

/**
 * Review, edit and confirm the extracted profile.
 *
 * Edits live in a local draft until "Save changes" sends them with the
 * profile's version (optimistic concurrency). Confirming saves pending edits
 * first and then asks the server to build the evidence index.
 */
export function ProfileReview({ profile }: ProfileReviewProps) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<ProfileDraft>(() => draftFromProfile(profile))
  const [titleErrors, setTitleErrors] = useState<Record<string, string>>({})
  const [focusKey, setFocusKey] = useState<string | null>(null)

  const dirty = isDirty(draft, profile)
  const openConflicts = unresolvedConflicts(profile.conflicts)
  const ready = isProfileReady(profile)

  /** Cache a profile returned by the server, discarding any poll still in flight. */
  async function storeProfile(next: Profile) {
    await queryClient.cancelQueries({ queryKey: queryKeys.profile })
    queryClient.setQueryData(queryKeys.profile, next)
  }

  const save = useMutation({
    mutationFn: (resolutions: ConflictResolutionInput[]) =>
      updateProfile(toPatchRequest(profile.version, draft, resolutions)),
    onSuccess: async (saved) => {
      await storeProfile(saved)
      setDraft(draftFromProfile(saved))
      // Drafts generated from the previous version are now stale.
      void queryClient.invalidateQueries({ queryKey: queryKeys.generations })
      toast.success('Changes saved')
    },
  })

  const confirm = useMutation({
    mutationKey: mutationKeys.confirmProfile,
    mutationFn: async () => {
      let version = profile.version
      if (dirty) {
        const saved = await updateProfile(toPatchRequest(version, draft))
        await storeProfile(saved)
        setDraft(draftFromProfile(saved))
        version = saved.version
      }
      return confirmProfile(version)
    },
    onSuccess: async (confirmed) => {
      await storeProfile(confirmed)
      void queryClient.invalidateQueries({ queryKey: queryKeys.session })
      void queryClient.invalidateQueries({ queryKey: queryKeys.generations })
      toast.success('Profile confirmed and evidence index built')
    },
    // The server records a failed indexing run on the profile; reload to show it.
    onError: () => void queryClient.invalidateQueries({ queryKey: queryKeys.profile }),
  })

  const busy = save.isPending || confirm.isPending

  /** Check the draft before any request; returns false and shows messages when it is invalid. */
  function draftIsValid(): boolean {
    const errors = validateDraft(draft)
    setTitleErrors(errors)
    return Object.keys(errors).length === 0
  }

  function handleSave() {
    if (draftIsValid()) save.mutate([])
  }

  function handleResolve(conflictId: string, resolution: 'resolved' | 'dismissed') {
    if (draftIsValid()) save.mutate([{ conflict_id: conflictId, resolution }])
  }

  function handleConfirm() {
    if (draftIsValid()) confirm.mutate()
  }

  /** After a version conflict: load the server's copy and drop local edits. */
  async function reloadLatest() {
    await queryClient.refetchQueries({ queryKey: queryKeys.profile })
    const latest = queryClient.getQueryData<Profile | null>(queryKeys.profile)
    if (latest) setDraft(draftFromProfile(latest))
    setTitleErrors({})
    save.reset()
    confirm.reset()
  }

  function changeContact(change: Partial<DraftContact>) {
    setDraft((current) => ({ ...current, contact: { ...current.contact, ...change } }))
  }

  function changeRecord(key: string, change: Partial<DraftRecord>) {
    setDraft((current) => ({
      ...current,
      records: current.records.map((record) =>
        record.key === key ? { ...record, ...change } : record,
      ),
    }))
  }

  function addRecord(category: RecordCategory) {
    const record = newRecord(category)
    setDraft((current) => ({ ...current, records: [...current.records, record] }))
    setFocusKey(record.key)
  }

  /** A stored record is only marked, so it can be restored; an unsaved one is simply dropped. */
  function removeRecord(record: DraftRecord) {
    if (record.record_id === null) {
      setDraft((current) => ({
        ...current,
        records: current.records.filter((item) => item.key !== record.key),
      }))
    } else {
      changeRecord(record.key, { removed: true })
    }
  }

  const needsReviewCount = profile.review_summary.needs_review_count
  const hasTitleErrors = Object.keys(titleErrors).length > 0
  const confirmBlocked = openConflicts.length > 0

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader
        title="Review your profile"
        description={
          <p>
            Everything here was extracted from your sources. Your documents can only use what
            you confirm on this page, so correct anything that is wrong and add what is missing.
          </p>
        }
      />

      <p className="text-sm text-muted-foreground">
        Sources:{' '}
        {profile.sources
          .map((source) => `${source.label} (${numberFormat.format(source.char_count)} characters)`)
          .join(', ')}
      </p>

      <IndexStatus
        profile={profile}
        isConfirming={confirm.isPending}
        confirmError={confirm.error}
        hasUnsavedChanges={dirty}
        onRetry={handleConfirm}
      />

      {needsReviewCount > 0 ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge
            status="needs_review"
            label={`${needsReviewCount} ${needsReviewCount === 1 ? 'item needs' : 'items need'} review`}
          />
          <span className="text-muted-foreground">
            These could not be matched to your sources with certainty. Check them below.
          </span>
        </div>
      ) : null}

      <ConflictPanel
        conflicts={profile.conflicts}
        records={profile.records}
        busy={busy}
        onResolve={handleResolve}
      />

      <fieldset disabled={busy} className="min-w-0 space-y-10">
        <legend className="sr-only">Profile details</legend>
        <ContactCard contact={draft.contact} onChange={changeContact} />

        {CATEGORY_SECTIONS.map((section) => {
          const records = draft.records.filter((record) => record.category === section.category)
          const headingId = `section-${section.category}`
          return (
            <section key={section.category} aria-labelledby={headingId} className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 id={headingId} className="text-xl font-medium">
                  {section.heading}
                </h2>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => addRecord(section.category)}
                >
                  <Plus aria-hidden="true" />
                  Add {section.singular}
                </Button>
              </div>
              {records.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing was found in your sources for this section.
                </p>
              ) : (
                records.map((record) => (
                  <RecordCard
                    key={record.key}
                    record={record}
                    original={profile.records.find((item) => item.record_id === record.record_id)}
                    section={section}
                    titleError={titleErrors[record.key]}
                    focusOnMount={record.key === focusKey}
                    onChange={(change) => changeRecord(record.key, change)}
                    onRemove={() => removeRecord(record)}
                    onRestore={() => changeRecord(record.key, { removed: false })}
                  />
                ))
              )}
            </section>
          )
        })}
      </fieldset>

      {save.isError ? (
        <ErrorAlert
          error={save.error}
          title="Your changes were not saved"
          onRetry={isApiError(save.error, 'version_conflict') ? undefined : handleSave}
        >
          {isApiError(save.error, 'version_conflict') ? (
            <Button type="button" variant="outline" size="sm" onClick={() => void reloadLatest()}>
              Reload latest version (discards unsaved edits)
            </Button>
          ) : null}
        </ErrorAlert>
      ) : null}

      <div className="sticky bottom-0 z-30 -mx-4 space-y-2 border-t bg-background/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-t-xl sm:border sm:border-b-0">
        {hasTitleErrors ? (
          <p role="alert" className="text-sm text-destructive">
            Some records have no title. Fix the highlighted fields, then try again.
          </p>
        ) : null}
        <p id="confirm-help" role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {confirmBlocked
            ? `Resolve or dismiss the ${openConflicts.length} open ${openConflicts.length === 1 ? 'conflict' : 'conflicts'} above before confirming.`
            : dirty
              ? 'You have unsaved changes.'
              : 'All changes are saved.'}
        </p>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={handleSave} disabled={!dirty || busy}>
            {save.isPending ? 'Saving...' : 'Save changes'}
          </Button>
          {ready && !dirty ? (
            <Button asChild>
              <Link to="/job">Continue to target job</Link>
            </Button>
          ) : (
            <Button
              type="button"
              onClick={handleConfirm}
              disabled={busy || confirmBlocked}
              aria-describedby="confirm-help"
            >
              {confirm.isPending ? 'Confirming...' : 'Confirm profile and build evidence index'}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
