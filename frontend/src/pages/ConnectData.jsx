import { useState } from 'react';
import '../styles/connectTabs.css';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Check, FileSpreadsheet, Server, ShieldCheck, Upload, X } from 'lucide-react';
import { useAppContext } from '../context/AppContext';
import { useAuth } from '../context/AuthContext';
import { fetchApi } from '../api/client';
import { can } from '../auth/capabilities';
import TallyImportPreview from '../components/connect/TallyImportPreview';
import HeaderRowPicker from '../components/connect/HeaderRowPicker';

const ACCEPTED_FILES = '.csv,.xlsx';
export const NUMBERS_FILE_MESSAGE =
  "Apple Numbers files can't be read. In Numbers choose File > Export To > Excel or CSV. From Tally, press Alt+E (Export), choose Excel, and upload that file.";

export function numbersFileError(files) {
  return files.some((file) => /\.numbers$/i.test(file.name)) ? NUMBERS_FILE_MESSAGE : '';
}

function importForm(file, fields) {
  const form = new FormData();
  form.append('file', file);
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== undefined && value !== null) form.append(key, String(value));
  });
  return form;
}

function fileLabel(file) {
  const size =
    file.size < 1024 * 1024
      ? `${Math.max(1, Math.round(file.size / 1024))} KB`
      : `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  return `${file.name} · ${size}`;
}

export default function ConnectData() {
  const { addWorkspace, setActiveWorkspaceId, workspaces = [] } = useAppContext();
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('upload');
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [workspaceName, setWorkspaceName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [createdWorkspace, setCreatedWorkspace] = useState(null);
  const [importSummary, setImportSummary] = useState(null);
  const [tallyPreview, setTallyPreview] = useState(null);
  const [tallyTarget, setTallyTarget] = useState('');
  const [headerPicker, setHeaderPicker] = useState(null);
  const [serverForm, setServerForm] = useState({
    name: '',
    engine: 'postgresql',
    host: '',
    port: '5432',
    database: '',
    username: '',
    password: '',
    ssl_required: true,
  });
  const [connecting, setConnecting] = useState(false);
  const [serverError, setServerError] = useState('');

  if (!can(user, 'manage_data_sources')) {
    return (
      <section className="connect-viewer-state">
        <ShieldCheck size={24} />
        <span>{user?.role || 'Member'} access</span>
        <h1>Data sources are protected.</h1>
        <p>
          Your role does not allow data source changes. Workspace owners and admins can upload or
          connect sources.
        </p>
        <Link to="/history">
          View query history <ArrowUpRight size={16} />
        </Link>
      </section>
    );
  }

  const tallySources = workspaces.filter((workspace) => workspace.source_type === 'tally_report');

  const resetImport = () => {
    setTallyPreview(null);
    setTallyTarget('');
    setHeaderPicker(null);
  };

  const selectFiles = (files) => {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    setSelectedFiles(incoming);
    setUploadError(numbersFileError(incoming));
    setCreatedWorkspace(null);
    setImportSummary(null);
    resetImport();
  };

  const finishImport = (workspace, summary = null) => {
    addWorkspace(workspace);
    setActiveWorkspaceId(workspace.id);
    setCreatedWorkspace(workspace);
    setImportSummary(summary);
    setSelectedFiles([]);
    setWorkspaceName('');
    resetImport();
  };

  const previewTally = async (file, targetId) => {
    const preview = await fetchApi('/workspaces/import/preview', {
      method: 'POST',
      body: importForm(file, { target_workspace_id: targetId }),
    });
    return preview;
  };

  const changeTallyTarget = async (targetId) => {
    if (!tallyPreview) return;
    setTallyTarget(targetId);
    setUploading(true);
    setUploadError('');
    try {
      const preview = await previewTally(tallyPreview.file, targetId);
      setTallyPreview({ file: tallyPreview.file, data: preview });
    } catch (error) {
      setUploadError(error.message || 'Could not check this report against the saved data.');
    } finally {
      setUploading(false);
    }
  };

  const confirmImport = async (fields) => {
    const file = tallyPreview?.file || headerPicker?.file;
    if (!file) return;
    setUploading(true);
    setUploadError('');
    try {
      const result = await fetchApi('/workspaces/import/confirm', {
        method: 'POST',
        body: importForm(file, { name: workspaceName.trim(), ...fields }),
      });
      finishImport(result.workspace, result.import);
    } catch (error) {
      setUploadError(error.message || 'Import failed');
    } finally {
      setUploading(false);
    }
  };

  const uploadFiles = async () => {
    if (!selectedFiles.length) return;
    const numbersError = numbersFileError(selectedFiles);
    if (numbersError) {
      setUploadError(numbersError);
      return;
    }
    setUploading(true);
    setUploadError('');
    try {
      if (selectedFiles.length === 1) {
        // A single file is checked first: Tally reports and title-row layouts
        // get a preview before anything is saved.
        const [file] = selectedFiles;
        const preview = await previewTally(file, '');
        if (preview.kind === 'tally') {
          setTallyPreview({ file, data: preview });
          return;
        }
        if (preview.kind === 'unknown') {
          setHeaderPicker({
            file,
            rows: preview.rows,
            selectedRow: preview.suggested_header_row || 0,
          });
          return;
        }
      }
      const form = new FormData();
      const multiple = selectedFiles.length > 1;
      selectedFiles.forEach((file) => form.append(multiple ? 'files' : 'file', file));
      form.append('name', workspaceName.trim());
      const workspace = await fetchApi(
        multiple ? '/workspaces/upload-multiple' : '/workspaces/upload',
        { method: 'POST', body: form },
      );
      finishImport(workspace);
    } catch (error) {
      setUploadError(error.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const updateServerForm = (field, value) =>
    setServerForm((current) => ({ ...current, [field]: value }));

  const connectServer = async (event) => {
    event.preventDefault();
    if (
      !serverForm.name.trim() ||
      !serverForm.host.trim() ||
      !serverForm.database.trim() ||
      !serverForm.username.trim() ||
      !serverForm.password
    ) {
      setServerError('Enter a workspace name and all connection details.');
      return;
    }
    const port = Number(serverForm.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setServerError('Enter a valid database port.');
      return;
    }
    setConnecting(true);
    setServerError('');
    try {
      const workspace = await fetchApi('/workspaces/server', {
        method: 'POST',
        body: JSON.stringify({
          ...serverForm,
          name: serverForm.name.trim(),
          host: serverForm.host.trim(),
          database: serverForm.database.trim(),
          username: serverForm.username.trim(),
          port,
        }),
      });
      addWorkspace(workspace);
      setActiveWorkspaceId(workspace.id);
      setCreatedWorkspace(workspace);
      setServerForm({
        name: '',
        engine: 'postgresql',
        host: '',
        port: '5432',
        database: '',
        username: '',
        password: '',
        ssl_required: true,
      });
    } catch (error) {
      setServerError(error.message || 'Could not connect to that database.');
    } finally {
      setConnecting(false);
    }
  };

  const displayWord = uploading
    ? 'SYNC'
    : selectedFiles.length
      ? 'READY'
      : activeTab === 'server'
        ? 'LINK'
        : 'DATA';

  return (
    <div className="connect-page">
      <section className="connect-stage">
        <header className="connect-stage-header">
          <span>Data sources</span>
          <span>Owner and admin access</span>
        </header>
        <div className="connect-stage-content">
          <svg
            className="connect-word"
            viewBox="0 0 1200 360"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
            focusable="false"
          >
            <text x="600" y="270" textAnchor="middle" pointerEvents="stroke">
              {displayWord}
            </text>
          </svg>
          <div className="connect-tablist" role="tablist" aria-label="Data source type">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'upload'}
              className={activeTab === 'upload' ? 'is-active' : ''}
              onClick={() => setActiveTab('upload')}
            >
              <Upload size={15} /> Upload files
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'server'}
              className={activeTab === 'server' ? 'is-active' : ''}
              onClick={() => setActiveTab('server')}
            >
              <Server size={15} /> Connect server
            </button>
          </div>

          {createdWorkspace ? (
            <div className="connect-complete" role="status">
              <div>
                <Check size={22} />
              </div>
              <span>Workspace ready</span>
              <h1>{createdWorkspace.name}</h1>
              <p>Your source is now isolated and available for verified, read-only analysis.</p>
              {importSummary?.kind === 'tally' && (
                <p className="connect-import-summary">
                  {importSummary.rows_added} row{importSummary.rows_added === 1 ? '' : 's'} added
                  {importSummary.rows_skipped
                    ? `, ${importSummary.rows_skipped} already saved and skipped`
                    : ''}
                  {importSummary.rows_replaced
                    ? `, ${importSummary.rows_replaced} replaced for the same date`
                    : ''}
                  .
                </p>
              )}
              <Link to="/ask" className="ui-action ui-action-primary">
                Ask your data <ArrowUpRight size={17} />
              </Link>
              <button type="button" onClick={() => setCreatedWorkspace(null)}>
                Add another source
              </button>
            </div>
          ) : activeTab === 'upload' ? (
            <div className="connect-upload-flow">
              <h1>
                Bring your data
                <br />
                into focus.
              </h1>
              <p>
                Upload CSV or Excel files, up to 20 at once. Puchoo.si builds one private, queryable
                workspace without mutating the source. Tally exports such as a Sales Register or
                Balance Sheet are recognised and checked before saving.
              </p>
              <input
                id="fileUpload"
                type="file"
                multiple
                accept={ACCEPTED_FILES}
                onChange={(event) => selectFiles(event.target.files)}
                hidden
              />
              <label
                htmlFor="fileUpload"
                className={`connect-dropzone${isDragging ? ' is-dragging' : ''}${uploading ? ' is-uploading' : ''}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDragging(false);
                  selectFiles(event.dataTransfer.files);
                }}
              >
                <div className="connect-dropzone-mark">
                  <Upload size={23} />
                </div>
                <strong>
                  {uploading
                    ? 'Creating isolated workspace…'
                    : selectedFiles.length
                      ? `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`
                      : 'Drop files here'}
                </strong>
                <span>
                  {uploading
                    ? 'Validating structure and access boundaries'
                    : 'or browse from your device'}
                </span>
              </label>

              {tallyPreview && (
                <TallyImportPreview
                  preview={tallyPreview.data}
                  fileName={tallyPreview.file.name}
                  tallySources={tallySources}
                  targetId={tallyTarget}
                  onTargetChange={changeTallyTarget}
                  onConfirm={() => confirmImport({ target_workspace_id: tallyTarget })}
                  onCancel={resetImport}
                  busy={uploading}
                />
              )}
              {headerPicker && (
                <HeaderRowPicker
                  fileName={headerPicker.file.name}
                  rows={headerPicker.rows}
                  selectedRow={headerPicker.selectedRow}
                  onSelect={(row) =>
                    setHeaderPicker((current) => ({ ...current, selectedRow: row }))
                  }
                  onConfirm={() => confirmImport({ header_row: headerPicker.selectedRow })}
                  onCancel={resetImport}
                  busy={uploading}
                />
              )}
              {selectedFiles.length > 0 && !uploading && !tallyPreview && !headerPicker && (
                <div className="connect-selected-files">
                  <div>
                    {selectedFiles.map((file) => (
                      <span key={`${file.name}-${file.size}`}>
                        <FileSpreadsheet size={14} />
                        <span className="connect-file-name">{fileLabel(file)}</span>
                        <button
                          type="button"
                          onClick={() =>
                            setSelectedFiles((current) => current.filter((item) => item !== file))
                          }
                          aria-label={`Remove ${file.name}`}
                        >
                          <X size={14} />
                        </button>
                      </span>
                    ))}
                  </div>
                  <input
                    value={workspaceName}
                    onChange={(event) => setWorkspaceName(event.target.value)}
                    placeholder="Name this workspace (optional)"
                    aria-label="Workspace name"
                  />
                  <button type="button" className="connect-submit" onClick={uploadFiles}>
                    Create workspace <ArrowUpRight size={17} />
                  </button>
                </div>
              )}
              {uploadError && (
                <p className="connect-error" role="alert">
                  {uploadError}
                </p>
              )}
            </div>
          ) : (
            <form className="connect-server-form" onSubmit={connectServer} noValidate>
              <h1>
                Connect a<br />
                trusted source.
              </h1>
              <p>
                Use a restricted database user. Puchoo.si validates every connection in read-only
                mode.
              </p>
              <div className="connect-server-grid">
                <label>
                  Workspace name
                  <input
                    type="text"
                    placeholder="Workspace name"
                    value={serverForm.name}
                    onChange={(event) => updateServerForm('name', event.target.value)}
                  />
                </label>
                <label>
                  Database engine
                  <select
                    value={serverForm.engine}
                    onChange={(event) => {
                      const engine = event.target.value;
                      updateServerForm('engine', engine);
                      updateServerForm('port', engine === 'mysql' ? '3306' : '5432');
                    }}
                    aria-label="Database engine"
                  >
                    <option value="postgresql">PostgreSQL</option>
                    <option value="mysql">MySQL</option>
                  </select>
                </label>
                <label>
                  Server host
                  <input
                    type="text"
                    placeholder="Host (db.example.com)"
                    value={serverForm.host}
                    onChange={(event) => updateServerForm('host', event.target.value)}
                  />
                </label>
                <label>
                  Database port
                  <input
                    type="number"
                    placeholder="Port"
                    min="1"
                    max="65535"
                    value={serverForm.port}
                    onChange={(event) => updateServerForm('port', event.target.value)}
                    aria-label="Database port"
                  />
                </label>
                <label>
                  Database name
                  <input
                    type="text"
                    placeholder="Database name"
                    value={serverForm.database}
                    onChange={(event) => updateServerForm('database', event.target.value)}
                  />
                </label>
                <label>
                  Read-only username
                  <input
                    type="text"
                    placeholder="Read-only username"
                    autoComplete="username"
                    value={serverForm.username}
                    onChange={(event) => updateServerForm('username', event.target.value)}
                  />
                </label>
                <label>
                  Password
                  <input
                    type="password"
                    placeholder="Password"
                    autoComplete="current-password"
                    value={serverForm.password}
                    onChange={(event) => updateServerForm('password', event.target.value)}
                  />
                </label>
              </div>
              <label className="connect-ssl">
                <input type="checkbox" checked readOnly disabled /> TLS is required by the server
              </label>
              {serverError && (
                <p className="connect-error" role="alert">
                  {serverError}
                </p>
              )}
              <button type="submit" className="connect-submit" disabled={connecting}>
                {connecting ? 'Checking connection…' : 'Connect source'} <ArrowUpRight size={17} />
              </button>
            </form>
          )}
        </div>
        <footer className="connect-stage-footer">
          <span>CSV · XLSX</span>
          <span>Source data is never altered</span>
        </footer>
      </section>
    </div>
  );
}
