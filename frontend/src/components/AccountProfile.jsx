import { useId, useRef, useState } from 'react';
import {
  Camera,
  Check,
  ChevronDown,
  Fingerprint,
  ImageUp,
  ShieldCheck,
  Trash2,
  User,
  X,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import ChangeEmailPanel from './auth/ChangeEmailPanel';
import '../styles/accountProfile.css';
import './profileReveal.css';

const MAX_PROFILE_IMAGE_BYTES = 200 * 1024;

export default function AccountProfile() {
  const { user, updateProfileImage } = useAuth();
  const detailsId = useId();
  const [expanded, setExpanded] = useState(false);
  const [showEmail, setShowEmail] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const revealRef = useRef(null);
  const emailSwitchRef = useRef(null);
  const workspaceName =
    user?.workspace_name ||
    user?.institute_name ||
    (['institution', 'institute'].includes(user?.workspace_type)
      ? 'School'
      : user?.workspace_type === 'business'
        ? 'Business'
        : 'Personal');

  function openDetails() {
    setExpanded(true);
    requestAnimationFrame(() => emailSwitchRef.current?.focus({ preventScroll: true }));
  }
  function closeDetails() {
    setExpanded(false);
    setShowEmail(false);
    setShowWorkspace(false);
    requestAnimationFrame(() => revealRef.current?.focus({ preventScroll: true }));
  }
  const fileInputRef = useRef(null);
  const [photoEditorOpen, setPhotoEditorOpen] = useState(false);
  const [photoDraft, setPhotoDraft] = useState(user?.image_url || '');
  const [photoError, setPhotoError] = useState('');
  const [photoSaving, setPhotoSaving] = useState(false);
  function openPhotoEditor() {
    setPhotoDraft(user?.image_url || '');
    setPhotoError('');
    setPhotoEditorOpen(true);
  }

  function closePhotoEditor() {
    setPhotoEditorOpen(false);
    setPhotoDraft(user?.image_url || '');
    setPhotoError('');
  }

  function choosePhoto(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
      setPhotoError('Choose a PNG, JPEG, WebP, or GIF image.');
      return;
    }
    if (file.size > MAX_PROFILE_IMAGE_BYTES) {
      setPhotoError('Choose an image smaller than 200 KB.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setPhotoDraft(String(reader.result || ''));
      setPhotoError('');
    };
    reader.onerror = () => setPhotoError('That image could not be read. Try another file.');
    reader.readAsDataURL(file);
  }

  async function savePhoto(imageUrl = photoDraft) {
    if (imageUrl && !imageUrl.startsWith('data:image/')) {
      setPhotoError('Upload a PNG, JPEG, WebP, or GIF image.');
      return;
    }
    setPhotoSaving(true);
    setPhotoError('');
    try {
      await updateProfileImage(imageUrl || null);
      setPhotoEditorOpen(false);
    } catch (error) {
      setPhotoError(error.message || 'The profile image could not be saved.');
    } finally {
      setPhotoSaving(false);
    }
  }

  const photoControls = (
    <>
      <div className="settings-photo-row">
        <div>
          <strong>Profile image</strong>
          <span>PNG, JPEG, WebP or GIF · maximum 200 KB</span>
        </div>
        <div className="settings-photo-entry-actions">
          <button type="button" className="settings-action" onClick={() => openPhotoEditor()}>
            <ImageUp size={15} /> Upload image
          </button>
        </div>
      </div>

      {photoEditorOpen && (
        <section className="settings-photo-editor" aria-label="Profile image editor">
          <div className="settings-photo-editor-head">
            <div>
              <span className="settings-section-kicker">Profile image</span>
              <h3>Upload your photo</h3>
            </div>
            <button
              type="button"
              className="settings-photo-close"
              onClick={closePhotoEditor}
              aria-label="Close profile image editor"
            >
              <X size={17} />
            </button>
          </div>
          <div className="settings-photo-workspace">
            <div className={`settings-photo-preview${photoDraft ? ' has-image' : ''}`}>
              {photoDraft ? (
                <img
                  src={photoDraft}
                  alt="Profile preview"
                  referrerPolicy="no-referrer"
                  onError={() => setPhotoError('This image cannot be loaded. Try another file.')}
                />
              ) : (
                <User size={26} />
              )}
            </div>
            <div className="settings-photo-source">
              <strong>Upload from your device</strong>
              <p>
                PNG, JPEG, WebP, or GIF up to 200 KB. Your image is stored securely with your
                Puchoo.si account.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                onChange={choosePhoto}
                hidden
              />
              <button
                type="button"
                className="settings-action"
                onClick={() => fileInputRef.current?.click()}
              >
                <ImageUp size={15} /> Choose image
              </button>
            </div>
          </div>
          {photoError && (
            <p className="settings-photo-error" role="alert">
              {photoError}
            </p>
          )}
          <footer className="settings-photo-actions">
            {user?.image_url && (
              <button
                type="button"
                className="settings-photo-remove"
                onClick={() => savePhoto(null)}
                disabled={photoSaving}
              >
                <Trash2 size={15} /> Remove image
              </button>
            )}
            <button type="button" className="settings-action" onClick={closePhotoEditor}>
              Cancel
            </button>
            <button
              type="button"
              className="settings-action settings-action-primary"
              onClick={() => savePhoto()}
              disabled={photoSaving || !photoDraft || Boolean(photoError)}
            >
              <Check size={15} /> {photoSaving ? 'Saving…' : 'Save image'}
            </button>
          </footer>
        </section>
      )}
    </>
  );

  return (
    <section
      className={`settings-section settings-profile account-profile-refined profile-reveal${expanded ? ' is-open' : ''}`}
      aria-labelledby="profile-title"
    >
      <div className="settings-card-label">
        <User size={15} />
        <span>Account profile</span>
        <small>{user?.role || 'member'}</small>
      </div>
      <div className="profile-reveal-window">
        <div className="profile-identity-sheet">
          <div
            className="profile-card-face profile-card-front"
            aria-hidden={expanded}
            inert={expanded}
          >
            <div className="profile-sheet-brand">
              <span>Puchoo.si</span>
              <Fingerprint size={28} aria-hidden="true" />
            </div>
            <div className="settings-profile-topline">
              <button
                type="button"
                className={`settings-avatar settings-avatar-button${user?.image_url ? ' has-image' : ''}`}
                onClick={() => openPhotoEditor()}
                aria-label="Change profile image"
              >
                {user?.image_url ? (
                  <img src={user.image_url} alt="Your profile" referrerPolicy="no-referrer" />
                ) : user?.full_name ? (
                  user.full_name.charAt(0).toUpperCase()
                ) : (
                  <User />
                )}
                <span className="settings-avatar-edit" aria-hidden="true">
                  <Camera size={12} />
                </span>
              </button>
              <div className="settings-profile-identity">
                <div className="settings-profile-titleline">
                  <h2 id="profile-title">{user?.full_name || 'User'}</h2>
                  <span className="settings-workspace-badge">
                    {['institution', 'institute'].includes(user?.workspace_type)
                      ? `School · ${user?.role || 'member'}`
                      : user?.workspace_type === 'business'
                        ? `Business · ${user?.role || 'member'}`
                        : `Personal · ${user?.role || 'owner'}`}
                  </span>
                </div>
              </div>
              <span className="settings-verified">
                <ShieldCheck size={14} /> Email verified
              </span>
            </div>
          </div>
          <div
            className="profile-card-face profile-card-back"
            aria-hidden={!expanded}
            inert={!expanded}
          >
            <div className="profile-sheet-brand">
              <span>Profile details</span>{' '}
              <button
                type="button"
                className="profile-close-details"
                aria-label="Hide profile details"
                aria-expanded={expanded}
                aria-controls={detailsId}
                onClick={closeDetails}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <h2 className="profile-back-name">{user?.full_name || 'User'}</h2>
            <div className="profile-sheet-data">
              <div>
                <span>Email</span>
                <strong
                  key={showEmail ? 'email-visible' : 'email-hidden'}
                  className={showEmail ? '' : 'is-masked'}
                  title={showEmail ? user?.email : undefined}
                >
                  {showEmail ? user?.email || 'Not available' : '••••••••••••'}
                </strong>
              </div>
              <div>
                <span>Workspace</span>
                <strong
                  key={showWorkspace ? 'workspace-visible' : 'workspace-hidden'}
                  className={showWorkspace ? '' : 'is-masked'}
                  title={showWorkspace ? workspaceName : undefined}
                >
                  {showWorkspace ? workspaceName : '••••••••'}
                </strong>
              </div>
            </div>
          </div>
        </div>
        <div className="profile-sleeve" aria-hidden="true">
          <svg viewBox="0 0 400 110" preserveAspectRatio="none">
            <path d="M0 6 C80 -3 125 23 200 23 C275 23 320 -3 400 6 V110 H0 Z" />
          </svg>
        </div>
        <button
          type="button"
          className="profile-reveal-trigger"
          ref={revealRef}
          hidden={expanded}
          onClick={openDetails}
          aria-expanded={expanded}
          aria-controls={detailsId}
        >
          <span>
            <User size={15} aria-hidden="true" />
            View profile details
          </span>
          <ChevronDown size={17} aria-hidden="true" />
        </button>
      </div>

      {!expanded && photoControls}

      <div
        className="profile-details-reveal"
        id={detailsId}
        aria-hidden={!expanded}
        inert={!expanded}
      >
        <div className="profile-details-inner">
          <div className="profile-visibility-controls" role="group" aria-label="Profile visibility">
            <button
              ref={emailSwitchRef}
              type="button"
              role="switch"
              aria-label="Show email"
              aria-checked={showEmail}
              aria-describedby={showEmail ? `${detailsId}-email-value` : undefined}
              className="profile-visibility-row"
              onClick={() => setShowEmail((visible) => !visible)}
            >
              <span className="profile-visibility-copy">
                <span>Show email</span>
                {showEmail && (
                  <span className="profile-visible-value" id={`${detailsId}-email-value`}>
                    {user?.email || 'Not available'}
                  </span>
                )}
              </span>
              <span className="profile-visibility-state" aria-hidden="true">
                {showEmail ? 'Visible' : 'Hidden'}
              </span>
              <span className="profile-visibility-track" aria-hidden="true">
                <span />
              </span>
            </button>
            <button
              type="button"
              role="switch"
              aria-label="Show workspace details"
              aria-checked={showWorkspace}
              aria-describedby={showWorkspace ? `${detailsId}-workspace-value` : undefined}
              className="profile-visibility-row"
              onClick={() => setShowWorkspace((visible) => !visible)}
            >
              <span className="profile-visibility-copy">
                <span>Show workspace details</span>
                {showWorkspace && (
                  <span className="profile-visible-value" id={`${detailsId}-workspace-value`}>
                    {workspaceName}
                  </span>
                )}
              </span>
              <span className="profile-visibility-state" aria-hidden="true">
                {showWorkspace ? 'Visible' : 'Hidden'}
              </span>
              <span className="profile-visibility-track" aria-hidden="true">
                <span />
              </span>
            </button>
          </div>
          <p className="profile-visibility-note">Show or mask details in this profile.</p>
          {expanded && photoControls}
          <div className="settings-section-footer profile-account-actions">
            <ChangeEmailPanel />
            <p className="profile-account-timezone">
              <span>Timezone</span>
              <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
