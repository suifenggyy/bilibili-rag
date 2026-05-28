"""
Bilibili RAG 知识库系统

数据模型定义
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from enum import Enum

Base = declarative_base()


# ==================== SQLAlchemy 模型 ====================

class VideoCache(Base):
    """视频内容缓存表"""
    __tablename__ = 'video_cache'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    bvid = Column(String(20), unique=True, index=True, nullable=False)
    cid = Column(Integer, nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    owner_name = Column(String(100), nullable=True)  # UP主名称
    owner_mid = Column(Integer, nullable=True)  # UP主ID
    
    # 内容
    content = Column(Text, nullable=True)  # 摘要/字幕文本
    content_source = Column(String(20), nullable=True)  # ai_summary / subtitle / basic_info
    outline_json = Column(JSON, nullable=True)  # 分段提纲
    
    # 元信息
    duration = Column(Integer, nullable=True)  # 视频时长（秒）
    pic_url = Column(String(500), nullable=True)  # 封面URL
    
    # 处理状态
    is_processed = Column(Boolean, default=False)  # 是否已处理并加入向量库
    process_error = Column(Text, nullable=True)  # 处理错误信息
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserSession(Base):
    """用户会话表"""
    __tablename__ = 'user_sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    
    # B站用户信息
    bili_mid = Column(Integer, nullable=True)  # B站用户ID
    bili_uname = Column(String(100), nullable=True)  # B站用户名
    bili_face = Column(String(500), nullable=True)  # 头像URL
    
    # Cookie 信息（加密存储更安全，这里简化处理）
    sessdata = Column(Text, nullable=True)
    bili_jct = Column(Text, nullable=True)
    dedeuserid = Column(String(50), nullable=True)
    
    # 状态
    is_valid = Column(Boolean, default=True)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class FavoriteFolder(Base):
    """收藏夹记录表"""
    __tablename__ = 'favorite_folders'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    
    # B站收藏夹信息  
    media_id = Column(Integer, nullable=False)  # 收藏夹ID
    fid = Column(Integer, nullable=True)  # 原始ID
    title = Column(String(200), nullable=False)
    media_count = Column(Integer, default=0)  # 视频数量
    
    # 状态
    is_selected = Column(Boolean, default=True)  # 是否选中用于知识库
    last_sync_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FavoriteVideo(Base):
    """收藏夹-视频关联表"""
    __tablename__ = 'favorite_videos'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    folder_id = Column(Integer, index=True, nullable=False)  # 关联 FavoriteFolder.id
    bvid = Column(String(20), index=True, nullable=False)
    
    # 是否选中（用户可以取消选中某些视频）
    is_selected = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class PlatformContentCache(Base):
    """通用内容缓存（YouTube / 小宇宙等新平台）"""
    __tablename__ = 'platform_content_cache'

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)       # youtube | xiaoyuzhou
    content_id = Column(String(300), nullable=False)    # 平台唯一 ID
    url = Column(String(500), nullable=True)
    title = Column(String(500), nullable=False)
    author = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    duration = Column(Integer, nullable=True)            # 秒
    cover_url = Column(String(500), nullable=True)

    content = Column(Text, nullable=True)               # ASR 转写文本
    content_source = Column(String(20), nullable=True)  # asr | basic_info

    is_processed = Column(Boolean, default=False)
    process_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('platform', 'content_id', name='uq_platform_content'),
    )


class YoutubeSource(Base):
    """YouTube 来源（频道 / 播放列表 / 单视频）"""
    __tablename__ = 'youtube_sources'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    source_type = Column(String(20), nullable=False)    # video | playlist | channel | liked | watch_later
    source_url = Column(String(500), nullable=False)
    title = Column(String(500), nullable=True)
    after_date = Column(String(10), nullable=True)      # YYYY-MM-DD，仅获取该日期之后的内容

    is_selected = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class YoutubeSession(Base):
    """YouTube 会话（Cookie 存储）"""
    __tablename__ = 'youtube_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    cookie_content = Column(Text, nullable=True)        # Netscape 格式 Cookie 文本
    cookie_file_path = Column(String(500), nullable=True)  # 本地 Cookie 文件路径

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PodcastSubscription(Base):
    """播客订阅表（小宇宙）"""
    __tablename__ = 'podcast_subscriptions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    podcast_id = Column(String(200), nullable=False)    # 小宇宙播客 PID
    title = Column(String(500), nullable=False)
    author = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    rss_url = Column(String(500), nullable=True)
    cover_url = Column(String(500), nullable=True)

    is_selected = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('session_id', 'podcast_id', name='uq_session_podcast'),
    )


class XiaoyuzhouSession(Base):
    """小宇宙登录会话（Token 存储）"""
    __tablename__ = 'xiaoyuzhou_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    phone = Column(String(20), nullable=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    uid = Column(String(100), nullable=True)
    nickname = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DouyinSession(Base):
    """抖音登录会话（Cookie 存储）"""
    __tablename__ = 'douyin_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    cookie_str = Column(Text, nullable=True)        # 完整 Cookie 字符串

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InstapaperSession(Base):
    """Instapaper 登录凭据存储"""
    __tablename__ = 'instapaper_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    consumer_key = Column(Text, nullable=True)
    consumer_secret = Column(Text, nullable=True)
    email = Column(String(200), nullable=True)
    password = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BiliCreator(Base):
    """B站 UP主配置（用于获取指定创作者的全部作品）"""
    __tablename__ = 'bili_creators'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    uid = Column(String(50), nullable=False)            # B站用户 UID（mid）
    nickname = Column(String(200), nullable=True)       # UP主昵称（展示用）
    after_date = Column(String(10), nullable=True)      # YYYY-MM-DD，仅获取该日期之后发布的视频

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('session_id', 'uid', name='uq_session_bili_creator'),
    )


class DouyinCreator(Base):
    """抖音创作者配置（用于获取指定创作者的全部作品）"""
    __tablename__ = 'douyin_creators'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    sec_uid = Column(String(300), nullable=False)       # 抖音用户 sec_uid 或主页 URL
    nickname = Column(String(200), nullable=True)       # 创作者昵称（展示用）
    after_date = Column(String(10), nullable=True)      # YYYY-MM-DD，仅获取该日期之后发布的视频

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('session_id', 'sec_uid', name='uq_session_douyin_creator'),
    )


class ContentProcessingRecord(Base):
    """统一内容处理状态记录（跨平台）"""
    __tablename__ = 'content_processing_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True)  # bilibili | youtube | xiaoyuzhou | douyin
    content_id = Column(String(300), nullable=False, index=True)  # bvid / video_id / episode_id / aweme_id
    title = Column(String(500), nullable=True)

    stage = Column(String(30), nullable=False, default='pending', index=True)
    # pending → asr_done → correction_done → completed | failed

    asr_raw_text = Column(Text, nullable=True)       # raw ASR before correction; kept for retry
    corrected_text = Column(Text, nullable=True)     # after text postprocessing
    summary_block = Column(Text, nullable=True)      # after summarization

    failed_stage = Column(String(20), nullable=True)  # asr | correction | index
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('platform', 'content_id', name='uq_platform_content_record'),
    )


class InboxEntry(Base):
    """知识库 inbox 处理记录（跨平台，用于幂等追踪和流水线状态）"""
    __tablename__ = 'inbox_entries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_platform = Column(String(30), nullable=False, index=True)
    source_identifier = Column(String(300), nullable=False)
    inbox_path = Column(String(1000), nullable=False)
    archive_path = Column(String(1000), nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(30), default="pending", index=True)  # pending/running/completed/failed
    category = Column(String(200), nullable=True)
    topics_json = Column(JSON, nullable=True)
    quality_score = Column(String(20), nullable=True)
    error_message = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== Pydantic 模型 (API 用) ====================

class ContentSource(str, Enum):
    """内容来源"""
    AI_SUMMARY = "ai_summary"
    SUBTITLE = "subtitle"
    BASIC_INFO = "basic_info"
    ASR = "asr"


class VideoInfo(BaseModel):
    """视频信息"""
    bvid: str
    cid: Optional[int] = None
    title: str
    description: Optional[str] = None
    owner_name: Optional[str] = None
    owner_mid: Optional[int] = None
    duration: Optional[int] = None
    pic_url: Optional[str] = None


class VideoContent(BaseModel):
    """视频内容（含摘要）"""
    bvid: str
    title: str
    content: str
    source: ContentSource
    outline: Optional[list] = None
    summary_block: Optional[str] = None
    asr_raw_text: Optional[str] = None  # 纠错前的原始 ASR 文本，供重试使用


class QRCodeResponse(BaseModel):
    """二维码响应"""
    qrcode_key: str
    qrcode_url: str
    qrcode_image_base64: str


class LoginStatusResponse(BaseModel):
    """登录状态响应"""
    status: str  # waiting / scanned / confirmed / expired
    message: str
    user_info: Optional[dict] = None
    session_id: Optional[str] = None


class FavoriteFolderInfo(BaseModel):
    """收藏夹信息"""
    media_id: int
    title: str
    media_count: int
    is_selected: bool = True
    is_default: Optional[bool] = None


class ChatRequest(BaseModel):
    """对话请求"""
    question: str
    session_id: Optional[str] = None
    folder_ids: Optional[list[int]] = None  # 指定收藏夹，None 表示全部


class ChatResponse(BaseModel):
    """对话响应"""
    answer: str
    sources: list[dict]  # 来源视频列表
