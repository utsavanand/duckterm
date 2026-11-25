Every handler under src/api validates its input before any I/O and returns a typed error object, never a bare string.
Endpoints are versioned in the path (/v1/...). A breaking change is a new version, not an edit.
