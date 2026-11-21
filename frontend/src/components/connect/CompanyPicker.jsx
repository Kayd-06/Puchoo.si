export const NEW_COMPANY = '__new__';

export function fileCountLabel(count) {
  if (typeof count !== 'number') return '';
  return `${count} file${count === 1 ? '' : 's'}`;
}

/** Uploaded sources that can take more files (not server connections, not wiped). */
export function companyOptions(workspaces = []) {
  return workspaces.filter(
    (workspace) => workspace.source_type !== 'server' && workspace.availability !== 'unavailable',
  );
}

/** The active company when it can take files, otherwise a new company. */
export function defaultCompanyTarget(companies, activeWorkspaceId) {
  return companies.some((company) => company.id === activeWorkspaceId)
    ? activeWorkspaceId
    : NEW_COMPANY;
}

export default function CompanyPicker({
  companies = [],
  value = NEW_COMPANY,
  onChange,
  newName = '',
  onNewNameChange,
  newNamePlaceholder = 'Company name, e.g. Demo Traders',
  disabled = false,
  idPrefix = 'company',
}) {
  return (
    <div className="company-picker">
      <label htmlFor={`${idPrefix}-target`}>
        <span>Add to company</span>
        <select
          id={`${idPrefix}-target`}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange?.(event.target.value)}
        >
          {companies.map((company) => (
            <option key={company.id} value={company.id}>
              {company.name}
              {typeof company.file_count === 'number'
                ? ` (${fileCountLabel(company.file_count)})`
                : ''}
            </option>
          ))}
          <option value={NEW_COMPANY}>+ New company</option>
        </select>
      </label>
      {value === NEW_COMPANY && (
        <label htmlFor={`${idPrefix}-new-name`}>
          <span>New company name</span>
          <input
            id={`${idPrefix}-new-name`}
            value={newName}
            disabled={disabled}
            placeholder={newNamePlaceholder}
            onChange={(event) => onNewNameChange?.(event.target.value)}
          />
        </label>
      )}
    </div>
  );
}
