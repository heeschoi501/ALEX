"""Office chat workflows: edit an owned attachment copy, or an approved local document (ADR-115).

Model output is a proposal. Authorization, source hashes, backups and undo stay in application code.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .documents import MAX_DOWNLOAD
from .harness.intent import FILE as CODE_FILE, REVERT, SKIP, permission_reply
from .officeedit import OfficeDocument, OfficeEditError, SUFFIXES, edit_messages, parse_edits, summary
from .providers import Message
from .router import Destination

EDIT = re.compile(r'고쳐|고치|수정해|수정하|다듬|교정해|바꿔|바꾸|채워|지워|삭제해|번역해서\s*바|추가해|추가하')
NAMES = re.compile(r'(?P<quoted>["\'][^"\'\r\n]+\.(?:docx|xlsx|pptx)["\'])|'
                   r'(?P<bare>[가-힣A-Za-z0-9_.:/\\-]+\.(?:docx|xlsx|pptx))(?![A-Za-z0-9_])', re.I)
NOTE = '원본의 표·그림·레이아웃을 유지합니다. 편집한 문단의 혼합 글자 서식은 일부 달라질 수 있습니다.'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OfficeEdits:
    def __init__(self, core):
        self.core = core
        self.base = core.storage.db_path.parent.resolve()

    def state(self, sid: str) -> dict:
        try:
            return json.loads(self.core.storage.get_state('office:' + sid) or '{}')
        except (ValueError, TypeError):
            return {}

    def save(self, sid: str, value: dict) -> None:
        self.core.storage.set_state('office:' + sid, json.dumps(value, ensure_ascii=False))

    def deactivate(self, sid: str) -> None:
        state = self.state(sid)
        if state.get('active'):
            state['active'] = False
            self.save(sid, state)

    def matches(self, sid: str, text: str) -> bool:
        if text.strip().startswith('/'):
            return False
        state = self.state(sid)
        if state.get('active') and (REVERT.search(text) or permission_reply(text)):
            return True
        if not EDIT.search(text) or CODE_FILE.search(text):
            return False
        if NAMES.search(text):
            return True
        docs = [d for d in self.core.knowledge.documents(scope='session:' + sid)
                if Path(d['title']).suffix.lower() in SUFFIXES]
        return bool(state.get('active') or docs and re.search(r'문서|자료|첨부|이거|이걸|이것|[0-9]+장|내용', text))

    def _folder(self, relative: str) -> Path:
        path = self.base / relative
        path.mkdir(parents=True, exist_ok=True)
        if path.resolve() != path.absolute() or self.base not in path.resolve().parents:
            raise OfficeEditError('문서 저장 폴더가 승인된 데이터 폴더 밖을 가리킵니다.')
        return path

    def _stored(self, relative: str) -> Path:
        path = self.base / relative
        if path.resolve() != path.absolute() or self.base not in path.resolve().parents:
            raise OfficeEditError('문서 사본 경로가 올바르지 않습니다.')
        return path

    def _pc(self, relative: str, expected_root: str = '') -> Path:
        root = self.core.current_project_root()
        if root is None:
            raise OfficeEditError('프로젝트 폴더를 먼저 지정해 주세요.')
        root = root.resolve()
        if expected_root and str(root) != expected_root:
            raise OfficeEditError('프로젝트 폴더가 바뀌었습니다. 문서 편집을 다시 요청해 주세요.')
        raw = Path(relative)
        path = raw if raw.is_absolute() else root / raw
        resolved = path.resolve()
        if root not in resolved.parents or resolved != path.absolute() or resolved.suffix.lower() not in SUFFIXES:
            raise OfficeEditError('승인된 프로젝트 안의 실제 Office 파일만 편집할 수 있습니다.')
        rel = resolved.relative_to(root)
        if SKIP.intersection(rel.parts) or any(p.startswith('.') for p in rel.parts):
            raise OfficeEditError('기록·설정·백업 폴더의 문서는 편집 대상이 아닙니다.')
        if not resolved.is_file():
            raise OfficeEditError('문서 파일을 찾지 못했습니다.')
        return resolved

    def _select(self, sid: str, text: str, state: dict) -> dict:
        names = [(m.group('quoted') or m.group('bare')).strip('"\'').replace('\\', '/') for m in NAMES.finditer(text)]
        docs = [d for d in self.core.knowledge.documents(scope='session:' + sid)
                if Path(d['title']).suffix.lower() in SUFFIXES]
        candidates = [d for d in docs if any(d['title'].casefold() == Path(n).name.casefold() for n in names)]
        attached = re.findall(r'\[첨부 문서:\s*(.*?)\s*·', text)
        if not attached and (any('/' in name or ':' in name for name in names)
                             or re.search(r'프로젝트|\bPC\b|그\s*자리', text, re.I)):
            candidates = []  # an explicit project path must not select a same-named attachment
        if attached:
            candidates = [d for d in docs if d['title'] in attached]
        if not names and not attached and state.get('active') and state.get('kind'):
            result = dict(state)
            result.pop('pending', None)
            return result
        if not names and not attached and len(docs) == 1:
            candidates = docs
        if len(candidates) == 1:
            d = candidates[0]
            previous = state if state.get('document') == d['id'] else {}
            return {'kind': 'copy', 'document': d['id'], 'title': d['title'], 'current': previous.get('current', '')}
        if candidates:
            raise OfficeEditError('문서가 여러 개입니다. 고칠 파일 이름을 하나만 지정해 주세요.')
        if not names:
            raise OfficeEditError('이 대화에 Office 파일을 첨부하거나 고칠 프로젝트 파일 이름을 지정해 주세요.')
        if self.core.member_mode:
            raise OfficeEditError('PC 문서 편집은 관리자만 가능합니다. 본인이 이 대화에 첨부한 문서는 사본으로 편집할 수 있습니다.')
        if len(set(names)) != 1:
            raise OfficeEditError('고칠 Office 파일을 하나씩 지정해 주세요.')
        name = names[0]
        root = self.core.current_project_root()
        if root is None:
            raise OfficeEditError('프로젝트 폴더를 먼저 지정해 주세요.')
        direct = Path(name) if Path(name).is_absolute() else root / name
        found = []
        if direct.is_file():
            found = [self._pc(name)]
        elif '/' not in name:
            seen = 0
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.') and not (Path(directory) / d).is_symlink()]
                seen += len(files)
                for f in files:
                    if f.casefold() == name.casefold():
                        found.append(self._pc(str(Path(directory) / f)))
                if seen > 20000:
                    break
        if len(found) != 1:
            raise OfficeEditError('고칠 문서를 하나로 찾지 못했습니다. 프로젝트 기준 상대 경로를 지정해 주세요.')
        path = found[0]
        previous = state if state.get('path') == str(path) else {}
        return {'kind': 'pc', 'path': str(path), 'root': str(root.resolve()), 'title': path.name,
                'undo': previous.get('undo', [])}

    def _source(self, sid: str, state: dict) -> Path:
        if state['kind'] == 'pc':
            if self.core.member_mode:
                raise OfficeEditError('PC 문서 편집은 관리자만 가능합니다.')
            return self._pc(state['path'], state['root'])
        self.core.knowledge.resolve_id(state['document'], ('session:' + sid,))
        if state.get('current'):
            return self._stored(state['current'])
        record = self.core.knowledge.document(state['document'])
        snapshot = record['snapshot']
        if Path(snapshot).name != snapshot:
            raise OfficeEditError('첨부 사본 경로가 올바르지 않습니다.')
        path = self.core.knowledge.sources / snapshot
        if path.resolve() != path.absolute():
            raise OfficeEditError('첨부 사본 경로가 올바르지 않습니다.')
        return path

    @staticmethod
    def _read(path: Path) -> bytes:
        if path.stat().st_size > MAX_DOWNLOAD:
            raise OfficeEditError('편집할 문서는 32MB 이하여야 합니다.')
        return path.read_bytes()

    @staticmethod
    def _replace(path: Path, data: bytes) -> None:
        temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.part')
        try:
            with temp.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _apply(self, sid: str, state: dict) -> str:
        pending = state['pending']
        source = self._source(sid, state)
        old = self._read(source)
        if digest(old) != pending['source_hash']:
            raise OfficeEditError('제안 이후 원본이 바뀌었습니다. 덮어쓰지 않았습니다. 편집을 다시 요청해 주세요.')
        draft = self._stored(pending['draft'])
        data = self._read(draft)
        if digest(data) != pending['result_hash']:
            raise OfficeEditError('편집 사본이 바뀌었습니다. 편집을 다시 요청해 주세요.')
        if state['kind'] == 'pc':
            folder = self._folder('backups/office')
            backup = folder / (datetime.now().strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(8) + source.suffix)
            backup.write_bytes(old)
            if digest(self._read(self._source(sid, state))) != pending['source_hash']:
                raise OfficeEditError('백업 중 원본이 바뀌었습니다. 덮어쓰지 않았습니다.')
            self._replace(source, data)
            state['undo'] = [*state.get('undo', [])[-4:], {'backup': str(backup.relative_to(self.base)),
                                                         'applied': digest(data)}]
            result = f"{state['title']}에 적용했습니다.\n\n{pending['summary']}\n\n'되돌려줘'로 원본을 복원할 수 있습니다."
        else:
            folder = self._folder('exports')
            name = 'alex-document-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(8) + source.suffix.lower()
            target = folder / name
            self._replace(target, data)
            state['current'] = str(target.relative_to(self.base))
            result = f"{state['title']}의 편집 사본을 만들었습니다.\n\n{pending['summary']}\n\n[문서 다운로드](alex-document:{name})"
        draft.unlink(missing_ok=True)
        state.pop('pending', None)
        state['active'] = True
        self.save(sid, state)
        return result + '\n\n' + NOTE

    def execute(self, sid: str, text: str) -> str:
        state = self.state(sid)
        try:
            if self.core.permission_mode == 'read':
                return '읽기 전용 권한에서는 문서를 편집하지 않습니다. 권한을 변경한 뒤 다시 요청해 주세요.'
            if state.get('active') and REVERT.search(text):
                if state.get('kind') != 'pc' or not state.get('undo'):
                    return '이 대화에서 되돌릴 PC 문서 변경이 없습니다. 첨부 원본은 변경하지 않았습니다.'
                source = self._source(sid, state)
                record = state['undo'][-1]
                if digest(self._read(source)) != record['applied']:
                    raise OfficeEditError('문서가 이후에 바뀌어 되돌리지 않았습니다. 백업을 직접 확인해 주세요.')
                self._replace(source, self._read(self._stored(record['backup'])))
                state['undo'].pop()
                self.save(sid, state)
                return f"{state['title']}을 편집 전 백업으로 되돌렸습니다."
            reply = permission_reply(text)
            if state.get('active') and reply and not state.get('pending'):
                return '승인을 기다리는 문서 수정안이 없습니다. 고칠 내용을 다시 지정해 주세요.'
            if state.get('active') and state.get('pending') and reply:
                if reply == 'reject':
                    self._stored(state['pending']['draft']).unlink(missing_ok=True)
                    state.pop('pending')
                    self.save(sid, state)
                    return '문서 수정안을 취소했습니다. 원본은 바꾸지 않았습니다.'
                # "항상" authorizes this proposal only; global/session permissions never change here.
                return self._apply(sid, state)
            selected = self._select(sid, text, state)
            source = self._source(sid, selected)
            original = self._read(source)
            doc = OfficeDocument(original, source.suffix)
            provider = self.core.providers[Destination.LOCAL_GENERAL]
            context = getattr(provider, 'num_ctx', 32768)
            output = getattr(provider, 'num_predict', 4096)
            room = max(4000, min(60000, (context - min(output, context // 2) - 2048) * 2 - len(text)))
            outline, cut = doc.outline(limit=room)
            messages = [Message(role, content) for role, content in edit_messages(outline, text, cut)]
            reply, destination, attempted = self.core._generate_local_only(messages, (Destination.LOCAL_GENERAL,))
            if not reply or destination is None:
                raise OfficeEditError('로컬 모델이 수정안을 만들지 못했습니다. 원본은 바꾸지 않았습니다.')
            edits = parse_edits(reply)
            visible = set(re.findall(r'^\[([^\]]+)\]', outline, re.M))
            if source.suffix.lower() != '.xlsx' and any(str(e.get('id')) not in visible for e in edits):
                raise OfficeEditError('모델이 읽지 못한 문단을 수정하려 해 적용하지 않았습니다. 범위를 나눠 요청해 주세요.')
            output, changes = doc.apply(edits)
            if not changes:
                return '적용할 문서 변경을 찾지 못했습니다. 원본은 바꾸지 않았습니다.'
            folder = self._folder('office/drafts')
            draft = folder / (secrets.token_hex(16) + source.suffix.lower())
            draft.write_bytes(output)
            selected.update(active=True, pending={'draft': str(draft.relative_to(self.base)), 'source_hash': digest(original),
                            'result_hash': digest(output), 'summary': summary(changes)[:5000]})
            if state.get('pending'):
                self._stored(state['pending']['draft']).unlink(missing_ok=True)
            self.save(sid, selected)
            if selected['kind'] == 'pc' and self.core.permission_mode != 'auto':
                return (f"{selected['title']} 수정안입니다(원본 미변경).\n\n{selected['pending']['summary']}\n\n"
                        "'응'이면 백업 후 적용하고, '아니'이면 취소합니다.\n\n" + NOTE)
            return self._apply(sid, selected)
        except (OfficeEditError, OSError, ValueError) as exc:
            self.core._answer_failed = True
            return '문서를 편집하지 못했습니다: ' + str(exc)
