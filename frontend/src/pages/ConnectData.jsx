import { useState } from 'react';
import '../styles/connectTabs.css';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  FileSpreadsheet,
  LoaderCircle,
  Server,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import { useAppContext } from '../context/AppContext';
import { useAuth } from '../context/AuthContext';
import { fetchApi } from '../api/client';
import { can } from '../auth/capabilities';
import TallyImportPreview, { TABLE_NAME_HINT } from '../components/connect/TallyImportPreview';
import HeaderRowPicker from '../components/connect/HeaderRowPicker';
import SourceIllustration from '../components/connect/SourceIllustration';
import BatchImport, {
  MAX_BATCH_FILES,
  TOO_MANY_FILES_MESSAGE,
} from '../components/connect/BatchImport';
import CompanyPicker, {
  NEW_COMPANY,
  companyOptions,
  defaultCompanyTarget,
} from '../components/connect/CompanyPicker';

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
  const {
    addWorkspace,
    updateWorkspace,
    setActiveWorkspaceId,
    activeWorkspaceId,
    workspaces = [],
  } = useAppContext();
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('upload');
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [workspaceName, setWorkspaceName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [createdWorkspace, setCreatedWorkspace] = useState(null);
  const [uploadedFileCount, setUploadedFileCount] = useState(0);
  // 'checking' while a file is only previewed, 'importing' once it is being saved.
  const [uploadPhase, setUploadPhase] = useState('checking');
  const [importSummary, setImportSummary] = useState(null);
  const [tallyPreview, setTallyPreview] = useState(null);
  const [companyTarget, setCompanyTarget] = useState(null);
  const [tableChoice, setTableChoice] = useState(null);
  const [appendInfo, setAppendInfo] = useState(null);
  const [mismatchConfirmed, setMismatchConfirmed] = useState(false);
  const [headerPicker, setHeaderPicker] = useState(null);
  const [plainTableName, setPlainTableName] = useState('');
  const [addedToCompany, setAddedToCompany] = useState(false);
  const [batchFiles, setBatchFiles] = useState(null);
  const [addMoreTo, setAddMoreTo] = useState('');
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

  const sourceWord =
    uploading || connecting
      ? 'SYNC'
      : createdWorkspace
        ? 'READY'
        : activeTab === 'server'
          ? 'LINK'
          : selectedFiles.length
            ? 'READY'
            : 'DATA';

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

  // A wiped source with no stored copy can't take more files until it is re-uploaded.
  const companies = companyOptions(workspaces);
  const target =
    companyTarget &&
    (companyTarget === NEW_COMPANY || companies.some((c) => c.id === companyTarget))
      ? companyTarget
      : defaultCompanyTarget(companies, activeWorkspaceId);
  const targetCompany = companies.find((company) => company.id === target) || null;
  const targetId = targetCompany ? targetCompany.id : '';

  const resetImport = () => {
    setTallyPreview(null);
    setHeaderPicker(null);
    setTableChoice(null);
    setAppendInfo(null);
    setMismatchConfirmed(false);
    setPlainTableName('');
  };

  const selectFiles = (files) => {
    const incoming = Array.from(files || []);
    if (!incoming.length || uploading) return;
    const numbersError = numbersFileError(incoming);
    if (numbersError) {
      setUploadError(numbersError);
      return;
    }
    if (incoming.length > MAX_BATCH_FILES) {
      setUploadError(
        `${TOO_MANY_FILES_MESSAGE} You chose ${incoming.length}; pick ${MAX_BATCH_FILES} or fewer.`,
      );
      return;
    }
    if (incoming.some((file) => !/\.(csv|xlsx)$/i.test(file.name))) {
      setUploadError('Choose CSV or XLSX files only.');
      return;
    }
    setAddMoreTo('');
    if (incoming.length > 1) {
      // Several files: every file gets its own preview before anything is saved.
      setSelectedFiles([]);
      setUploadError('');
      setCreatedWorkspace(null);
      setImportSummary(null);
      resetImport();
      setBatchFiles(incoming);
      return;
    }
    setSelectedFiles(incoming);
    setUploadError(numbersFileError(incoming));
    setCreatedWorkspace(null);
    setImportSummary(null);
    resetImport();
  };

  const finishImport = (workspace, summary = null, existing = false, fileCount = 1) => {
    if (existing) updateWorkspace(workspace);
    else addWorkspace(workspace);
    setActiveWorkspaceId(workspace.id);
    setCreatedWorkspace(workspace);
    setImportSummary(summary);
    setUploadedFileCount(fileCount);
    setAddedToCompany(existing);
    setSelectedFiles([]);
    setWorkspaceName('');
    resetImport();
  };

  const previewTally = async (file, previewTargetId) => {
    const preview = await fetchApi('/workspaces/import/preview', {
      method: 'POST',
      body: importForm(file, { target_workspace_id: previewTargetId }),
    });
    return preview;
  };

  const showTallyPreview = (file, preview) => {
    setTallyPreview({ file, data: preview });
    setTableChoice({
      mode: preview.company?.suggested_mode || 'new',
      table: preview.company?.suggested_table || preview.table,
      label: '',
    });
    setAppendInfo(null);
    setMismatchConfirmed(false);
    if (!workspaceName.trim() && preview.company_name) setWorkspaceName(preview.company_name);
  };

  const changeTallyTarget = async (nextTarget) => {
    if (!tallyPreview) return;
    setCompanyTarget(nextTarget);
    setUploadPhase('checking');
    setUploading(true);
    setUploadError('');
    try {
      const preview = await previewTally(
        tallyPreview.file,
        nextTarget === NEW_COMPANY ? '' : nextTarget,
      );
      showTallyPreview(tallyPreview.file, preview);
    } catch (error) {
      setUploadError(error.message || 'Could not check this report against the saved data.');
    } finally {
      setUploading(false);
    }
  };

  const changeTableChoice = async (next) => {
    setTableChoice(next);
    if (!tallyPreview || !targetId || next.mode !== 'append' || !next.table) {
      setAppendInfo(null);
      return;
    }
    try {
      const info = await fetchApi('/workspaces/import/append-check', {
        method: 'POST',
        body: importForm(tallyPreview.file, {
          target_workspace_id: targetId,
          table_name: next.table,
        }),
      });
      setAppendInfo(info);
    } catch {
      setAppendInfo(null);
    }
  };

  const confirmImport = async (fields) => {
    const file = tallyPreview?.file || headerPicker?.file;
    if (!file) return;
    setUploadPhase('importing');
    setUploading(true);
    setUploadError('');
    try {
      const result = await fetchApi('/workspaces/import/confirm', {
        method: 'POST',
        body: importForm(file, {
          ...(targetId ? { target_workspace_id: targetId } : { name: workspaceName.trim() }),
          ...fields,
        }),
      });
      finishImport(result.workspace, result.import, Boolean(result.added_to_existing));
    } catch (error) {
      setUploadError(
        error.code === 'company_mismatch'
          ? `${error.message} Tick the check box above to add it anyway.`
          : error.message || 'Import failed',
      );
    } finally {
      setUploading(false);
    }
  };

  const confirmTally = () =>
    confirmImport({
      mode: tableChoice?.mode || '',
      table_name: tableChoice?.table || '',
      label: tableChoice?.mode === 'new' ? tableChoice?.label || '' : '',
      confirm_company_mismatch: mismatchConfirmed ? 'true' : '',
    });

  const uploadFiles = async () => {
    if (!selectedFiles.length) return;
    const numbersError = numbersFileError(selectedFiles);
    if (numbersError) {
      setUploadError(numbersError);
      return;
    }
    setUploadPhase(selectedFiles.length === 1 ? 'checking' : 'importing');
    setUploading(true);
    setUploadError('');
    try {
      if (selectedFiles.length === 1) {
        // A single file is checked first: Tally reports and title-row layouts
        // get a preview before anything is saved.
        const [file] = selectedFiles;
        const preview = await previewTally(file, targetId);
        if (preview.kind === 'tally') {
          showTallyPreview(file, preview);
          return;
        }
        if (preview.kind === 'unknown') {
          setHeaderPicker({
            file,
            rows: preview.rows,
            selectedRow: preview.suggested_header_row || 0,
          });
          setPlainTableName(preview.company?.suggested_table || '');
          return;
        }
      }
      setUploadPhase('importing');
      const form = new FormData();
      const multiple = selectedFiles.length > 1;
      selectedFiles.forEach((file) => form.append(multiple ? 'files' : 'file', file));
      if (targetId) form.append('target_workspace_id', targetId);
      else form.append('name', workspaceName.trim());
      const workspace = await fetchApi(
        multiple ? '/workspaces/upload-multiple' : '/workspaces/upload',
        { method: 'POST', body: form },
      );
      finishImport(workspace, null, Boolean(targetId), selectedFiles.length);
    } catch (error) {
      setUploadError(error.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const batchSaved = (workspace, existing) => {
    if (existing) updateWorkspace(workspace);
    else addWorkspace(workspace);
    setActiveWorkspaceId(workspace.id);
  };

  const addMoreFiles = (companyId) => {
    setBatchFiles(null);
    setCompanyTarget(companyId);
    setAddMoreTo(companyId);
    setSelectedFiles([]);
    setUploadError('');
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
      setUploadedFileCount(0);
      setAddedToCompany(false);
      setImportSummary(null);
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

  return (
    <div className={`connect-page${batchFiles && activeTab === 'upload' ? ' is-batch' : ''}`}>
      <section className="connect-stage">
        <header className="connect-stage-header">
          <span>Data sources</span>
          <span>Owner and admin access</span>
        </header>
        <div className="connect-stage-content">
          <aside className="connect-intro">
            <span className="connect-eyebrow">YOUR KNOWLEDGE, CONNECTED</span>
            <div className="connect-signal" data-state={sourceWord} aria-hidden="true">
              <span key={sourceWord} className="connect-signal-word">
                {sourceWord}
              </span>
            </div>
            <h2>
              A clear path
              <br />
              to better answers.
            </h2>
            <p>
              Bring your files or connect a database. Your next insight starts with a trusted
              source.
            </p>
            <div className="connect-trust-item">
              <ShieldCheck size={19} />
              <div>
                <strong>Private by design</strong>
                <span>Data stays within your workspace.</span>
              </div>
            </div>
            <div className="connect-trust-item">
              <FileSpreadsheet size={19} />
              <div>
                <strong>Ready for questions</strong>
                <span>Turn structured data into useful answers.</span>
              </div>
            </div>
            <div className="connect-intro-note">
              READ-ONLY ANALYSIS<span>Your source data stays unchanged.</span>
            </div>
          </aside>
          <div className="connect-workbench">
            <div className="connect-tablist" role="tablist" aria-label="Data source type">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'upload'}
                className={activeTab === 'upload' ? 'is-active' : ''}
                onClick={() => setActiveTab('upload')}
                disabled={uploading || connecting}
              >
                <Upload size={15} /> Upload files
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'server'}
                className={activeTab === 'server' ? 'is-active' : ''}
                onClick={() => setActiveTab('server')}
                disabled={uploading || connecting}
              >
                <Server size={15} /> Connect server
              </button>
            </div>

            {batchFiles && activeTab === 'upload' ? (
              <BatchImport
                files={batchFiles}
                companies={companies}
                initialTarget={target}
                onCompanySaved={batchSaved}
                onClose={() => setBatchFiles(null)}
                onAddMore={addMoreFiles}
              />
            ) : createdWorkspace ? (
              <div className="connect-complete" role="status">
                <div className="connect-complete-art">
                  <SourceIllustration
                    state="success"
                    variant={createdWorkspace.source_type === 'server' ? 'link' : 'upload'}
                    fileCount={uploadedFileCount}
                  />
                </div>
                <h1>
                  {createdWorkspace.source_type === 'server'
                    ? 'Connection Successful'
                    : 'Import Successful'}
                </h1>
                <p className="connect-complete-name">{createdWorkspace.name} · Workspace ready</p>
                <p>
                  {createdWorkspace.source_type === 'server'
                    ? 'Your source is now isolated and available for verified, read-only analysis.'
                    : addedToCompany
                      ? `${uploadedFileCount === 1 ? 'The file is' : `${uploadedFileCount} files are`} now part of ${createdWorkspace.name}${
                          typeof createdWorkspace.file_count === 'number'
                            ? `, which has ${createdWorkspace.file_count} file${createdWorkspace.file_count === 1 ? '' : 's'}`
                            : ''
                        }. Ask questions across all of them.`
                      : `${uploadedFileCount} ${uploadedFileCount === 1 ? 'file' : 'files'} uploaded. Your source is now isolated and available for verified, read-only analysis.`}
                </p>
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
                  Add another file
                </button>
              </div>
            ) : activeTab === 'upload' ? (
              <div className="connect-upload-flow">
                <h1 className="connect-sr-only">Upload your data</h1>
                {addMoreTo && targetCompany?.id === addMoreTo && (
                  <p className="connect-upload-note" role="status">
                    Choose more files for {targetCompany.name}. They are added next to the{' '}
                    {targetCompany.file_count ? `${targetCompany.file_count} files` : 'files'}{' '}
                    already there.
                  </p>
                )}
                <input
                  id="fileUpload"
                  type="file"
                  multiple
                  accept={ACCEPTED_FILES}
                  onChange={(event) => {
                    selectFiles(event.target.files);
                    // Allow choosing the same file again after an error or reset.
                    event.target.value = '';
                  }}
                  disabled={uploading}
                  className="connect-file-input"
                />
                <label
                  htmlFor="fileUpload"
                  className={`connect-dropzone${isDragging ? ' is-dragging' : ''}${uploading ? ' is-uploading' : ''}`}
                  aria-busy={uploading}
                  onDragOver={(event) => {
                    event.preventDefault();
                    if (!uploading) setIsDragging(true);
                  }}
                  onDragLeave={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    setIsDragging(false);
                    selectFiles(event.dataTransfer.files);
                  }}
                >
                  <SourceIllustration
                    state={uploading ? 'uploading' : 'idle'}
                    phase={uploadPhase}
                    fileCount={selectedFiles.length}
                  />
                  <strong className="connect-upload-title">
                    {uploading && (
                      <LoaderCircle
                        size={20}
                        className="connect-upload-spinner"
                        aria-hidden="true"
                      />
                    )}
                    {uploading
                      ? uploadPhase === 'importing'
                        ? 'Importing your file…'
                        : 'Checking your file…'
                      : selectedFiles.length
                        ? `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`
                        : 'Upload Files'}
                  </strong>
                  <span className="connect-dropzone-description">
                    {uploading ? (
                      'Validating your data and preparing your workspace'
                    ) : (
                      <>
                        Drag and drop your files here, or <b>click to select.</b>
                      </>
                    )}
                  </span>
                  <span className="connect-dropzone-formats">Supported formats: .xlsx, .csv</span>
                </label>
                <p className="connect-sr-only" role="status">
                  {uploading
                    ? uploadPhase === 'importing'
                      ? 'Importing your file'
                      : 'Checking your file'
                    : ''}
                </p>

                <p className="connect-upload-note">
                  Upload CSV or Excel files, up to {MAX_BATCH_FILES} at once.
                </p>
                <p className="connect-upload-note">
                  Tally exports (XLSX or CSV) and plain spreadsheets are supported. Each file gets a
                  preview; you can cancel individual files and save the rest into one company.
                  Adding files never deletes existing data.
                </p>

                {tallyPreview && (
                  <TallyImportPreview
                    preview={tallyPreview.data}
                    fileName={tallyPreview.file.name}
                    companies={companies}
                    targetId={target}
                    onTargetChange={changeTallyTarget}
                    newCompanyName={workspaceName}
                    onNewCompanyNameChange={setWorkspaceName}
                    choice={tableChoice}
                    onChoiceChange={changeTableChoice}
                    appendInfo={appendInfo}
                    mismatchConfirmed={mismatchConfirmed}
                    onMismatchConfirmedChange={setMismatchConfirmed}
                    onConfirm={confirmTally}
                    onCancel={resetImport}
                    busy={uploading}
                  />
                )}
                {headerPicker && (
                  <>
                    {targetId && (
                      <label className="connect-table-name" htmlFor="header-table-name">
                        <span>Table name in {targetCompany?.name}</span>
                        <input
                          id="header-table-name"
                          value={plainTableName}
                          maxLength={63}
                          onChange={(event) => setPlainTableName(event.target.value)}
                        />
                        <small>{TABLE_NAME_HINT}</small>
                      </label>
                    )}
                    <HeaderRowPicker
                      fileName={headerPicker.file.name}
                      rows={headerPicker.rows}
                      selectedRow={headerPicker.selectedRow}
                      onSelect={(row) =>
                        setHeaderPicker((current) => ({ ...current, selectedRow: row }))
                      }
                      onConfirm={() =>
                        confirmImport({
                          header_row: headerPicker.selectedRow,
                          table_name: targetId ? plainTableName.trim() : '',
                        })
                      }
                      onCancel={resetImport}
                      busy={uploading}
                    />
                  </>
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
                    <CompanyPicker
                      idPrefix="upload-company"
                      companies={companies}
                      value={target}
                      onChange={setCompanyTarget}
                      newName={workspaceName}
                      onNewNameChange={setWorkspaceName}
                    />
                    <button type="button" className="connect-submit" onClick={uploadFiles}>
                      {targetCompany ? `Add to ${targetCompany.name}` : 'Create company'}{' '}
                      <ArrowUpRight size={17} />
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
                <div className="connect-server-art">
                  <SourceIllustration variant="link" state={connecting ? 'uploading' : 'idle'} />
                </div>
                <h1>Connect your database</h1>
                <p>Link PostgreSQL or MySQL using a read-only database user.</p>
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
                  {connecting ? 'Checking connection…' : 'Connect source'}{' '}
                  <ArrowUpRight size={17} />
                </button>
              </form>
            )}
          </div>
        </div>
        <footer className="connect-stage-footer">
          <span>CSV · XLSX</span>
          <span>Source data is never altered</span>
        </footer>
      </section>
    </div>
  );
}
