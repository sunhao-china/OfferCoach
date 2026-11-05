"""提示词注册表：版本化模板的加载与渲染（string.Template，纯替换）。"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

import yaml
from pydantic import BaseModel

_VERSION_RE = re.compile(r"^(?P<name>.+)\.v(?P<version>\d+)$")


class PromptNotFoundError(LookupError):
    """提示词不存在（未知名称或版本）。"""


class PromptRenderError(ValueError):
    """渲染失败（变量缺失等）。"""


class PromptSpec(BaseModel):
    """一个版本的提示词。"""

    name: str
    version: int
    description: str = ""
    template: str


class PromptRegistry:
    """从模板目录加载全部 `*.vN.yaml`，按 (name, version) 索引。"""

    def __init__(self, templates_dir: str | Path) -> None:
        self._specs: dict[tuple[str, int], PromptSpec] = {}
        base = Path(templates_dir)
        for path in sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.yml")):
            m = _VERSION_RE.match(path.stem)
            if not m:
                continue  # 非模板文件（如目录说明）
            rel = path.parent.relative_to(base)
            derived_name = (
                f"{rel.as_posix()}/{m.group('name')}" if rel.as_posix() != "." else m.group("name")
            )
            derived_version = int(m.group("version"))
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            spec = PromptSpec(**data)
            if spec.name != derived_name or spec.version != derived_version:
                raise ValueError(
                    f"提示词文件 {path.name} 内容声明 ({spec.name} v{spec.version}) "
                    f"与文件名推导 ({derived_name} v{derived_version}) 不一致"
                )
            self._specs[(spec.name, spec.version)] = spec

    def get(self, name: str, version: int | None = None) -> PromptSpec:
        if version is not None:
            spec = self._specs.get((name, version))
            if spec is None:
                available = sorted(v for n, v in self._specs if n == name)
                raise PromptNotFoundError(
                    f"提示词 '{name}' v{version} 不存在（可用版本: {available or '无'}）"
                )
            return spec
        versions = [v for n, v in self._specs if n == name]
        if not versions:
            raise PromptNotFoundError(f"提示词 '{name}' 不存在")
        return self._specs[(name, max(versions))]

    def names(self) -> list[str]:
        """全部提示词名（去重升序；供 Langfuse 推送等外部工具枚举）。"""
        return sorted({n for n, _ in self._specs})

    def versions(self, name: str) -> list[int]:
        """指定提示词的可用版本升序。"""
        return sorted(v for n, v in self._specs if n == name)

    def render(self, prompt_name: str, version: int | None = None, **variables: str) -> str:
        # 注：变量名不得与 prompt_name / version 参数同名（get 内部同名即冲突）
        spec = self.get(prompt_name, version)
        try:
            return Template(spec.template).substitute(variables)
        except KeyError as exc:
            raise PromptRenderError(
                f"提示词 '{spec.name}' v{spec.version} 缺少变量: {exc.args[0]}"
            ) from exc
