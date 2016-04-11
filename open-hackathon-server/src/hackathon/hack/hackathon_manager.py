# -*- coding: utf-8 -*-
#
# -----------------------------------------------------------------------------------
# Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# -----------------------------------------------------------------------------------

import sys

sys.path.append("..")
import imghdr
import uuid
from datetime import timedelta

from werkzeug.exceptions import PreconditionFailed, InternalServerError, BadRequest
from flask import g, request
import lxml
from lxml.html.clean import Cleaner
from mongoengine import Q

from hackathon.hmongo.models import Hackathon, UserHackathon, DockerHostServer, User, HackathonNotice, HackathonStat, \
    Organization, Award, Team
from hackathon.hackathon_response import internal_server_error, ok, not_found, general_error, HTTP_CODE
from hackathon.constants import HACKATHON_CONFIG, HACK_USER_TYPE, HACK_STATUS, HACK_USER_STATUS, HTTP_HEADER, \
    FILE_TYPE, HACK_TYPE, HACKATHON_STAT, DockerHostServerStatus, HACK_NOTICE_CATEGORY, HACK_NOTICE_EVENT, \
    ORGANIZATION_TYPE, CLOUD_PROVIDER
from hackathon import RequiredFeature, Component, Context

docker_host_manager = RequiredFeature("docker_host_manager")
__all__ = ["HackathonManager"]

util = RequiredFeature("util")


class HackathonManager(Component):
    """Component to manage hackathon

    Note that it only handle operations directly related to Hackathon table. Things like registerd users, templates are
    in separated components
    """

    admin_manager = RequiredFeature("admin_manager")
    user_manager = RequiredFeature("user_manager")
    register_manager = RequiredFeature("register_manager")

    # basic xss prevention
    cleaner = Cleaner(safe_attrs=lxml.html.defs.safe_attrs | set(['style']))  # preserve style

    def is_hackathon_name_existed(self, name):
        """Check whether hackathon with specific name exists or not

        :type name: str|unicode
        :param name: name of hackathon

        :rtype: bool
        :return True if hackathon with specific name exists otherwise False
        """
        hackathon = self.get_hackathon_by_name(name)
        return hackathon is not None

    def is_recycle_enabled(self, hackathon):
        key = HACKATHON_CONFIG.RECYCLE_ENABLED
        return self.util.str2bool(self.get_basic_property(hackathon, key, False))

    def get_hackathon_by_name(self, name):
        """Get hackathon accoring the unique name

        :type name: str|unicode
        :param name: name of hackathon

        :rtype: Hackathon
        :return hackathon instance if found else None
        """
        if not name:
            return None

        return Hackathon.objects(name=name).first()

    def get_hackathon_by_id(self, hackathon_id):
        """Query hackathon by id
        :type hackathon_id: str or ObjectId are both ok
        :param hackathon_id: _id of hackathon

        :return hackathon instance or None
        """
        return Hackathon.objects(id=hackathon_id).first()

    def get_hackathon_detail(self, hackathon):
        user = None
        if self.user_manager.validate_login():
            user = g.user

        return self.__get_hackathon_detail(hackathon, user)

    def get_hackathon_stat(self, hackathon):

        def internal_get_stat():
            return self.__get_hackathon_stat(hackathon)

        cache_key = "hackathon_stat_%s" % hackathon.id
        return self.cache.get_cache(key=cache_key, createfunc=internal_get_stat)

    # TODO: implement HackathonStat related features: order_by == 'registered_users_num':
    def get_hackathon_list(self, args):
        # get values from request's QueryString
        page = int(args.get("page", 1))
        per_page = int(args.get("per_page", 20))
        order_by = args.get("order_by", "create_time")
        status = args.get("status")
        name = args.get("name")

        # build query by search conditions and order_by
        status_filter = Q()
        name_filter = Q()
        order_by_condition = '-id'

        if status:
            status_filter = Q(status=status)
        if name:
            name_filter = Q(name__contains=name)

        if order_by == 'create_time':
            order_by_condition = '-create_time'
        elif order_by == 'event_start_time':
            order_by_condition = '-event_start_time'
        elif order_by == 'registered_users_num':
            # hackathons with zero registered users would not be shown.
            # TODO
            pass
        else:
            order_by_condition = '-id'

        # perform db query with pagination
        pagination = Hackathon.objects(status_filter & name_filter).order_by(order_by_condition).paginate(page,
                                                                                                          per_page)

        user = None
        if self.user_manager.validate_login():
            user = g.user

        def func(hackathon):
            return self.__get_hackathon_detail(hackathon, user)

        # return serializable items as well as total count
        return self.util.paginate(pagination, func)

    def get_online_hackathons(self):
        return Hackathon.objects(status=HACK_STATUS.ONLINE)

    def get_user_hackathon_list_with_detail(self, user_id):
        user = self.user_manager.get_user_by_id(user_id)
        user_hack_list = self.db.session().query(Hackathon) \
            .join(UserHackathonRel, UserHackathonRel.hackathon_id == Hackathon.id) \
            .filter(UserHackathonRel.deleted != 1, UserHackathonRel.user_id == user_id).all()

        return map(lambda h: self.__get_hackathon_detail(h, user), user_hack_list)

    def get_recyclable_hackathon_list(self):
        # todo fix auto recycle
        # all_hackathon = self.db.find_all_objects(Hackathon)
        # return filter(lambda h: self.is_recycle_enabled(h), all_hackathon)
        hackathons = Hackathon.objects().all()
        return filter(lambda h: self.is_recycle_enabled(h), hackathons)
        #return []

    def get_entitled_hackathon_list_with_detail(self, user):
        hackathon_ids = self.admin_manager.get_entitled_hackathon_ids(user.id)
        if -1 in hackathon_ids:
            hackathon_list = self.db.find_all_objects(Hackathon)
        else:
            hackathon_list = self.db.find_all_objects(Hackathon, Hackathon.id.in_(hackathon_ids))

        return map(lambda h: self.__get_hackathon_detail(h, user), hackathon_list)

    def get_basic_property(self, hackathon, key, default=None):
        """Get basic property of hackathon from HackathonConfig"""
        if hackathon.config:
            return hackathon.config.get(key, default)
        return default

    def get_all_properties(self, hackathon):
        configs = self.db.find_all_objects_by(HackathonConfig, hackathon_id=hackathon.id)
        return [c.dic() for c in configs]

    def set_basic_property(self, hackathon, properties):
        """Set basic property in table HackathonConfig"""

        hackathon.config.update(properties)
        hackathon.save()

        self.cache.invalidate(self.__get_config_cache_key(hackathon))
        return ok()

    def delete_basic_property(self, hackathon, keys):
        if isinstance(keys, str):
            keys = keys.split()

        map(lambda key: hackathon.config.pop(key, None), keys)

        hackathon.save()
        self.cache.invalidate(self.__get_config_cache_key(hackathon))
        return ok()

    def delete_property(self, hackathon, key):
        self.db.delete_all_objects_by(HackathonConfig, hackathon_id=hackathon.id, key=key)
        return ok()

    def get_recycle_minutes(self, hackathon):
        key = HACKATHON_CONFIG.RECYCLE_MINUTES
        minutes = self.get_basic_property(hackathon, key, 60)
        return int(minutes)

    def validate_hackathon_name(self):
        if HTTP_HEADER.HACKATHON_NAME in request.headers:
            try:
                hackathon_name = request.headers[HTTP_HEADER.HACKATHON_NAME]
                hackathon = Hackathon.objects(name=hackathon_name).first()
                if hackathon:
                    g.hackathon = hackathon
                    return True
                else:
                    self.log.debug("cannot find hackathon by name %s" % hackathon_name)
                    return False
            except Exception as ex:
                self.log.error(ex)
                self.log.debug("hackathon_name invalid")
                return False
        else:
            self.log.debug("hackathon_name not found in headers")
            return False

    def create_new_hackathon(self, context):
        """Create new hackathon based on the http body

        Hackathon name is unique so duplicated names are not allowd.

        :type context: Context
        :param context: the body of http request that contains fields to create a new hackathon

        :rtype: dict
        """
        if Hackathon.objects(name=context.name).count() > 0:
            raise PreconditionFailed("hackathon name already exists")

        self.log.debug("add a new hackathon:" + context.name)
        new_hack = self.__create_hackathon(g.user, context)

        # init data is for local only
        if self.util.is_local():
            self.__create_default_data_for_local(new_hack)

        self.create_hackathon_notice(new_hack.id, HACK_NOTICE_EVENT.HACK_CREATE, HACK_NOTICE_CATEGORY.HACKATHON)

        return new_hack.dic()

    def update_hackathon(self, args):
        """Update hackathon properties

        :type args: dict
        :param args: arguments from http request body that contains properties with new values

        :rtype dict
        :return hackathon in dict if updated successfully.
        """
        hackathon = g.hackathon

        try:
            update_items = self.__parse_update_items(args, hackathon)
            self.log.debug("update hackathon items :" + str(args.keys()))

            if 'config' in update_items:
                self.set_basic_property(hackathon, update_items.get('config', {}))
                update_items.pop('config', None)

            if 'status' in update_items and int(update_items['status']) == HACK_STATUS.ONLINE:
                self.create_hackathon_notice(hackathon.id, HACK_NOTICE_EVENT.HACK_ONLINE,
                                             HACK_NOTICE_CATEGORY.HACKATHON)  # hackathon online

            # basic xss prevention
            if 'description' in update_items and update_items['description']:
                update_items['description'] = self.cleaner.clean_html(update_items['description'])
                self.log.debug("hackathon description :" + update_items['description'])

            hackathon.modify(**update_items)
            hackathon.save()

            return ok()
        except Exception as e:
            self.log.error(e)
            return internal_server_error("fail to update hackathon")

    def upload_files(self):
        """Handle uploaded files from http request"""
        self.__validate_upload_files()

        images = []
        storage = RequiredFeature("storage")
        for file_name in request.files:
            file_storage = request.files[file_name]
            self.log.debug("upload image file : " + file_name)
            context = Context(
                hackathon_name=g.hackathon.name,
                file_name=file_storage.filename,
                file_type=FILE_TYPE.HACK_IMAGE,
                content=file_storage
            )
            context = storage.save(context)
            image = {
                "name": file_storage.filename,
                "url": context.url,
                "thumbnailUrl": context.url,
                "deleteUrl": '/api/admin/file?key=' + context.file_name
            }
            # context.file_name is a random name created by server, file.filename is the original name
            images.append(image)

        return {"files": images}

    def get_userlike_all_hackathon(self, user_id):
        user_hackathon_rels = UserHackathon.objects(user=user_id).all()

        def get_user_hackathon_detail(user_hackathon_rel):
            dict = user_hackathon_rel.dic()
            dict["hackathon_info"] = user_hackathon_rel.hackathon.dic()
            return dict

        return [get_user_hackathon_detail(rel) for rel in user_hackathon_rels]

    def like_hackathon(self, user, hackathon):
        user_hackathon = UserHackathon.objects(hackathon=hackathon, user=user).first()
        if not user_hackathon:
            user_hackathon = UserHackathon(hackathon=hackathon,
                                           user=user,
                                           role=HACK_USER_TYPE.VISITOR,
                                           status=HACK_USER_STATUS.UNAUDIT,
                                           like=True,
                                           remark="")
            user_hackathon.save()
        if not user_hackathon.like:
            user_hackathon.like = True
            user_hackathon.save()

        # increase the count of users that like this hackathon
        self.increase_hackathon_stat(hackathon, HACKATHON_STAT.LIKE, 1)

        return ok()

    def unlike_hackathon(self, user, hackathon):
        user_hackathon = UserHackathon.objects(user=user, hackathon=hackathon).first()
        if user_hackathon:
            user_hackathon.like = False
            user_hackathon.save()

        # sync the like count
        like_count = UserHackathon.objects(hackathon=hackathon, like=True).count()
        self.update_hackathon_stat(hackathon, HACKATHON_STAT.LIKE, like_count)
        return ok()

    def update_hackathon_stat(self, hackathon, stat_type, count):
        """Increase or descrease the count for certain hackathon stat

        :type hackathon: Hackathon
        :param hackathon: instance of Hackathon to be counted

        :type stat_type: str|unicode
        :param stat_type: type of stat that defined in constants.py#HACKATHON_STAT

        :type count: int
        :param count: the new count for this stat item
        """
        stat = HackathonStat.objects(hackathon=hackathon, type=stat_type).first()
        if stat:
            stat.count = count
            stat.update_time = self.util.get_now()
        else:
            stat = HackathonStat(hackathon=hackathon, type=stat_type, count=count)

        if stat.count < 0:
            stat.count = 0
        stat.save()

    def increase_hackathon_stat(self, hackathon, stat_type, increase):
        """Increase or descrease the count for certain hackathon stat

        :type hackathon: Hackathon
        :param hackathon: instance of Hackathon to be counted

        :type stat_type: str|unicode
        :param stat_type: type of stat that defined in constants.py#HACKATHON_STAT

        :type increase: int
        :param increase: increase of the count. Can be positive or negative
        """
        stat = HackathonStat.objects(hackathon=hackathon, type=stat_type).first()
        if stat:
            stat.count += increase
        else:
            stat = HackathonStat(hackathon=hackathon, type=stat_type, count=increase)

        if stat.count < 0:
            stat.count = 0
        stat.update_time = self.util.get_now()
        stat.save()

    def get_hackathon_tags(self, hackathon):
        tags = self.db.find_all_objects_by(HackathonTag, hackathon_id=hackathon.id)
        return ",".join([t.tag for t in tags])

    def set_hackathon_tags(self, hackathon, tags):
        """Set hackathon tags

        :type tags: list
        :param tags: a list of str, every str is a tag
        """
        self.db.delete_all_objects_by(HackathonTag, hackathon_id=hackathon.id)
        for tag in tags:
            t = tag.strip('"').strip("'")
            self.db.add_object(HackathonTag(tag=t, hackathon_id=hackathon.id))
        self.db.commit()
        return ok()

    def get_distinct_tags(self):
        """Return all distinct hackathon tags for auto-complete usage"""
        return self.db.session().query(HackathonTag.tag).distinct().all()

    def qet_organizer_by_id(self, organizer_id):
        organizer = self.db.get_object(HackathonOrganizer, organizer_id)
        if organizer:
            return organizer.dic()
        return not_found()

    def create_hackathon_organizer(self, hackathon, body):
        organizer = Organization(id=uuid.uuid4(),
                                 name=body["name"],
                                 description=body["description"],
                                 homepage=body["homepage"],
                                 logo=body["logo"])
        if int(body["organization_type"]) == ORGANIZATION_TYPE.ORGANIZER:
            hackathon.update(push__organizers=organizer)
        else:
            hackathon.update(push__partners=organizer)

        hackathon.update_time = self.util.get_now()
        hackathon.save()
        return ok()

    def update_hackathon_organizer(self, hackathon, body):
        previous_organization_type = ORGANIZATION_TYPE.ORGANIZER if \
            hackathon.organizers.filter(id=body["id"]) else ORGANIZATION_TYPE.PARTNER

        organizer = hackathon.organizers.get(id=body["id"]) if \
            previous_organization_type == ORGANIZATION_TYPE.ORGANIZER else \
            hackathon.partners.get(id=body["id"])

        if not organizer:
            return not_found()

        if not previous_organization_type == body.get("organization_type", previous_organization_type):
            if previous_organization_type == ORGANIZATION_TYPE.ORGANIZER:
                hackathon.update(pull__organizers=organizer)
                hackathon.update(push__partners=organizer)
            else:
                hackathon.update(pull__partners=organizer)
                hackathon.update(push__organizers=organizer)

        organizer.name = body.get("name", organizer.name)
        organizer.description = body.get("description", organizer.description)
        organizer.homepage = body.get("homepage", organizer.homepage)
        organizer.logo = body.get("logo", organizer.logo)
        organizer.save()

        hackathon.update_time = self.util.get_now()
        hackathon.save()
        return ok()

    def delete_hackathon_organizer(self, hackathon, organizer_id):
        if hackathon.organizers.filter(id=organizer_id):
            hackathon.update(pull__organizers=hackathon.organizers.get(id=organizer_id))
        else:
            hackathon.update(pull__partners=hackathon.partners.get(id=organizer_id))

        hackathon.update_time = self.util.get_now()
        hackathon.save()
        return ok()

    def create_hackathon_award(self, hackathon, body):
        level = int(body.get("level"))
        if level > 10:
            level = 10

        award = Award(id=uuid.uuid4(),
                      name=body.get("name"),
                      description=body.get("description"),
                      level=level,
                      quota=body.get("quota"),
                      award_url=body.get("award_url"))
        hackathon.update(push__awards=award)

        hackathon.update_time = self.util.get_now()
        hackathon.save()
        return ok()

    def update_hackathon_award(self, hackathon, body):
        award = hackathon.awards.get(id=body.get("id"))
        if not award:
            return not_found("award not found")

        level = award.level
        if "level" in body:
            level = int(body.get("level"))
            if level > 10:
                level = 10

        award.name = body.get("name", award.name)
        award.description = body.get("description", award.description)
        award.level = body.get("level", level)
        award.quota = body.get("quota", award.quota)
        award.award_url = body.get("award_url", award.award_url)
        award.save()

        hackathon.update_time = self.util.get_now()
        hackathon.save()
        return ok()

    def delete_hackathon_award(self, hackathon, award_id):
        award = hackathon.awards.get(id=award_id)
        hackathon.update(pull__awards=award)
        hackathon.update_time = self.util.get_now()
        hackathon.save()

        # delete granted award in teams
        award_uuid = uuid.UUID(award_id)
        Team.objects(hackathon=hackathon, awards=award_uuid).update(pull__awards=award_uuid)

        return ok()

    def list_hackathon_awards(self, hackathon):
        awards = hackathon.dic()["awards"]
        awards.sort(key=lambda award: -award["level"])
        return awards

    def get_hackathon_notice(self, notice_id):
        hackathon_notice = HackathonNotice.objects(id=notice_id).first()
        if not hackathon_notice:
            return not_found("hackathon_notice not found")

        return hackathon_notice.dic()

    def create_hackathon_notice(self, hackathon_id, notice_event, notice_category, body={}):
        """
        create hackathon notice with hackathon_id, notice_event, notice_category.
        notice 'content' and 'link' can be included in body (optional)

        :type hackathon_id: int
        :param hackathon_id: id of hackathon that the notice belongs to (-1 if the notice doesn't belong to a specfic hackathon)

        :type notice_event: Class HACK_NOTICE_EVENT
        :param notice_event: event that the notice is triggered by, used for notice filtering (see get_hackathon_notice_list())
                             more specfic than notice_category, new events can be added without disturbing front-end code

        :type notice_category: Class HACK_NOTICE_CATEGORY
        :param notice_category: category that the notice belongs to, used for notice filtering and notice properties display
                                at front-end (e.g. icons/descriptions, see oh.manage.notice.js & oh.site.hackathon.js),
                                more general than notice_event, if you want to add a new category in HACK_NOTICE_CATEGORY,
                                remember to update front-end js code as well.

        :type body: dict/Context, default value: {}
        :param body: other necessary information, e.g.: 'content'(notice's content), 'link'(notice's link), other keys for specfic uses

        :return: hackathon_notice in dict

        ::Example:
        :create_hackathon_notice(2, HACK_NOTICE_EVENT.xx, HACK_NOTICE_CATEGORY.yy, {'content': 'zz'})
            a new notice for a hackathon with id 2 is created for the propose of HACK_NOTICE_EVENT.xx. The notice's front-end icon
            and description is determined by HACK_NOTICE_CATEGORY.yy, while its content is 'zz' and its link url is ''

        :create_hackathon_notice(-1, HACK_NOTICE_EVENT.xx, HACK_NOTICE_CATEGORY.yy)
            a new notice not belongs to any hackathon is created for the propose of HACK_NOTICE_EVENT.xx. The notice's front-end icon
            and description is determined by HACK_NOTICE_CATEGORY.yy, while its content and link url is ''
        """
        hackathon_notice = HackathonNotice(content='',
                                           link='',
                                           event=notice_event,
                                           category=notice_category)

        hackathon = self.get_hackathon_by_id(hackathon_id)
        if hackathon:
            hackathon_notice.hackathon = hackathon

        # notice creation logic for different notice_events
        if hackathon:
            if notice_event == HACK_NOTICE_EVENT.HACK_CREATE:
                hackathon_notice.content = u"Hachathon: %s 创建成功" % (hackathon.name)
            elif notice_event == HACK_NOTICE_EVENT.HACK_EDIT and hackathon:
                hackathon_notice.content = u"Hachathon: %s 信息变更" % (hackathon.name)
            elif notice_event == HACK_NOTICE_EVENT.HACK_ONLINE and hackathon:
                hackathon_notice.content = u"Hachathon: %s 正式上线" % (hackathon.name)
            elif notice_event == HACK_NOTICE_EVENT.HACK_OFFLINE and hackathon:
                hackathon_notice.content = u"Hachathon: %s 下线" % (hackathon.name)
            else:
                pass

        if notice_event == HACK_NOTICE_EVENT.EXPR_JOIN and body.get('user_id'):
            user_id = body.get('user_id')
            user = self.user_manager.get_user_by_id(user_id)
            hackathon_notice.content = u"用户 %s 开始编程" % (user.nickname)
        else:
            pass

        # use assigned value if content or link is assigned in body
        hackathon_notice.content = body.get('content', hackathon_notice.content)
        hackathon_notice.link = body.get('link', hackathon_notice.link)

        hackathon_notice.save(validate=False)

        self.log.debug("a new notice is created: hackathon: %s, event: %d, category: %d" % (
            hackathon.name, notice_event, notice_category))
        return hackathon_notice.dic()

    def update_hackathon_notice(self, body):
        hackathon_notice = HackathonNotice.objects(id=body.get('id')).first()
        if not hackathon_notice:
            return not_found("hackathon_notice not found")

        hackathon_notice.content = body.get("content", hackathon_notice.content)
        hackathon_notice.link = body.get("link", hackathon_notice.link)

        hackathon_notice.save(validate=False)
        return hackathon_notice.dic()

    def delete_hackathon_notice(self, notice_id):
        hackathon_notice = HackathonNotice.objects(id=notice_id).first()
        if not hackathon_notice:
            return not_found('Hackathon notice not found')

        hackathon_notice.delete()
        return ok()

    def get_hackathon_notice_list(self, body):
        """
        list hackathon notices, notices are paginated, can be filtered by hackathon_name, event and category,
        can be ordered by update_time, event and category.

        :type body: Context
        :param body: valid key/values(all key/values are optional)
            body = {
                hackathon_name: string,                  // filter by hackathon_name, default unfiltered
                category: 'int[,int...]',                // filter by category, default unfiltered
                event: 'int[,int...]',                   // filter by event, default unfiltered
                order_by: 'time' | 'event' | 'category', // order by update_time, event, category, default by time
                page: int,                               // page number after pagination, start from 1, default 1
                per_page: int                            // items per page, default 1000
            }

        :return: json style text, see util.Utility

        ::Example:
        : body = { order_by: 'time', category: '1,2,3', page: 1, per_page: 6 }
            search first 6 notices ordered by time, filtered by: category in [1,2,3]
        : body = { hackathon_name: 'hackathon', event: '1', order_by: 'event' }
            search first 1000 notices ordered by event, filtered by event == 1 and hackathon_name == 'hackathon'
        """

        hackathon_name = body.get("hackathon_name")
        notice_category = body.get("category")
        notice_event = body.get("event")
        order_by = body.get("order_by", "time")
        page = int(body.get("page", 1))
        per_page = int(body.get("per_page", 1000))

        hackathon_filter = Q()
        category_filter = Q()
        event_filter = Q()
        order_by_condition = '-update_time'

        if hackathon_name:
            hackathon = Hackathon.objects(name=hackathon_name).only('name').first()
            if hackathon:
                hackathon_filter = Q(hackathon=hackathon)
            else:
                return not_found('hackathon_name not found')

        if notice_category:
            notice_category_tuple = tuple([int(category) for category in notice_category.split(',')])
            category_filter = Q(category__in=notice_category_tuple)
        if notice_event:
            notice_event_tuple = tuple([int(event) for event in notice_event.split(',')])
            event_filter = Q(event__in=notice_event_tuple)

        if order_by == 'category':
            order_by_condition = '+category'
        elif order_by == 'event':
            order_by_condition = '+event'
        else:
            order_by_condition = '-update_time'

        pagination = HackathonNotice.objects(
            hackathon_filter & category_filter & event_filter
        ).order_by(
            order_by_condition
        ).paginate(page, per_page)

        def func(hackathon_notice):
            return hackathon_notice.dic()

        # return serializable items as well as total count
        return self.util.paginate(pagination, func)

    def schedule_pre_allocate_expr_job(self):
        """Add an interval schedule job to check all hackathons"""
        next_run_time = self.util.get_now() + timedelta(seconds=3)
        self.scheduler.add_interval(feature="hackathon_manager",
                                    method="check_hackathon_for_pre_allocate_expr",
                                    id="check_hackathon_for_pre_allocate_expr",
                                    next_run_time=next_run_time,
                                    minutes=10)

    def check_hackathon_for_pre_allocate_expr(self):
        """Check all hackathon for pre-allocate

        Add an interval job for hackathon if it's pre-allocate is enabled.
        Otherwise try to remove the schedule job
        """
        # todo fix pre-allocate
        # hackathon_list = self.db.find_all_objects(Hackathon)
        #hackathon_list = []
        hackathon_list = Hackathon.objects().all()
        for hack in hackathon_list:
            job_id = "pre_allocate_expr_" + str(hack.id)
            is_job_exists = self.scheduler.has_job(job_id)
            if hack.is_pre_allocate_enabled():
                if is_job_exists:
                    self.log.debug("pre_allocate job already exists for hackathon %s" % str(hack.id))
                    continue

                self.log.debug("add pre_allocate job for hackathon %s" % str(hack.id))
                next_run_time = self.util.get_now() + timedelta(seconds=hack.id * 10)
                pre_allocate_interval = self.__get_pre_allocate_interval(hack)
                self.scheduler.add_interval(feature="expr_manager",
                                            method="pre_allocate_expr",
                                            id=job_id,
                                            context=Context(hackathon_id=hack.id),
                                            next_run_time=next_run_time,
                                            seconds=pre_allocate_interval
                                            )
            elif is_job_exists:
                self.log.debug("remove job for hackathon %s since pre_allocate is disabled" % str(hack.id))
                self.scheduler.remove_job(job_id)
        return True

    def hackathon_online(self, hackathon):
        req = ok()

        if hackathon.status == HACK_STATUS.DRAFT or hackathon.status == HACK_STATUS.OFFLINE:
            if self.util.is_local() or hackathon.config.cloud_provide == CLOUD_PROVIDER.NONE:
                req = ok()
            elif hackathon.config.cloud_provider == CLOUD_PROVIDER.AZURE:
                is_success = docker_host_manager.check_subscription_id(hackathon.id)
                if not is_success:
                    req = general_error(code=HTTP_CODE.AZURE_KEY_NOT_READY)  # azure sub id is invalide

        elif hackathon.status == HACK_STATUS.ONLINE:
            req = ok()
        else:
            req = general_error(code=HTTP_CODE.CREATE_NOT_FINISHED)

        if req.get('error') is None:
            hackathon.status = HACK_STATUS.ONLINE
            hackathon.save()

        return req

    def hackathon_offline(self, hackathon):
        req = ok()
        if hackathon.status == HACK_STATUS.ONLINE or hackathon.status == HACK_STATUS.DRAFT:
            hackathon.status = HACK_STATUS.OFFLINE
            hackathon.save()
        elif hackathon.status == HACK_STATUS.INIT:
            req = general_error(code=HTTP_CODE.CREATE_NOT_FINISHED)

        return req

    # TODO: we need to review those commented items one by one to decide the API output
    def __get_hackathon_detail(self, hackathon, user=None):
        """Return hackathon info as well as its details including configs, stat, organizers, like if user logon"""
        detail = hackathon.dic()

        # TODO: replace hard code
        detail["stat"] = {"register": 5}

        if user:
            detail["user"] = self.user_manager.user_display_info(user)
            detail["user"]["is_admin"] = user.is_super or hackathon.creator.id == user.id

            # TODO: we need to review those items one by one to decide the API output
            # asset = self.db.find_all_objects_by(UserHackathonAsset, user_id=user.id, hackathon_id=hackathon.id)
            # if asset:
            #     detail["asset"] = [o.dic() for o in asset]

            # like = self.db.find_first_object_by(HackathonLike, user_id=user.id, hackathon_id=hackathon.id)
            # if like:
            #     detail["like"] = like.dic()

            register = self.register_manager.get_registration_by_user_and_hackathon(user.id, hackathon.id)
            if register:
                detail["registration"] = register.dic()
                #
                # team_rel = self.db.find_first_object_by(UserTeamRel, user_id=user.id, hackathon_id=hackathon.id)
                # if team_rel:
                #     detail["team"] = team_rel.team.dic()

        return detail

    def __create_hackathon(self, creator, context):
        """Insert hackathon and creator(admin of course) to database

        We enforce that default config are used during the creation

        :type context: Context
        :param context: context of the args to create a new hackathon

        :rtype: Hackathon
        :return hackathon instance
        """

        new_hack = Hackathon(
            name=context.name,
            display_name=context.display_name,
            ribbon=context.get("ribbon"),
            description=context.get("description"),
            short_description=context.get("short_description"),
            location=context.get("location"),
            banners=context.get("banners", []),
            status=HACK_STATUS.INIT,
            creator=creator,
            type=context.get("type", HACK_TYPE.HACKATHON),
            config=context.get("config", Context()).to_dict(),
            tags=context.get("tags", []),
            event_start_time=context.get("event_start_time"),
            event_end_time=context.get("event_end_time"),
            registration_start_time=context.get("registration_start_time"),
            registration_end_time=context.get("registration_end_time"),
            judge_start_time=context.get("judge_start_time"),
            judge_end_time=context.get("judge_end_time")
        )

        # basic xss prevention
        if new_hack.description:  # case None type
            new_hack.description = self.cleaner.clean_html(new_hack.description)

        # insert into table hackathon
        new_hack.save()

        # add the current login user as admin and creator
        try:
            admin = UserHackathon(user=creator,
                                  hackathon=new_hack,
                                  role=HACK_USER_TYPE.ADMIN,
                                  status=HACK_USER_STATUS.AUTO_PASSED,
                                  remark='creator')
            admin.save()
        except Exception as ex:
            # TODO: send out a email to remind administrator to deal with this problems
            self.log.error(ex)
            raise InternalServerError("fail to create the default administrator")

        return new_hack

    def __get_pre_allocate_interval(self, hackathon):
        interval = self.get_basic_property(hackathon, HACKATHON_CONFIG.PRE_ALLOCATE_INTERVAL_SECONDS)
        if interval:
            return int(interval)
        else:
            return 300 + hackathon.id * 10

    def __get_hackathon_configs(self, hackathon):

        def __internal_get_config():
            configs = {}
            for c in hackathon.configs.all():
                configs[c.key] = c.value
            return configs

        cache_key = self.__get_config_cache_key(hackathon)
        return self.cache.get_cache(key=cache_key, createfunc=__internal_get_config)

    def __get_hackathon_organizers(self, hackathon):
        organizers = self.db.find_all_objects_by(HackathonOrganizer, hackathon_id=hackathon.id)
        return [o.dic() for o in organizers]

    def __parse_update_items(self, args, hackathon):
        """Parse properties that need to update

        Only those whose value changed items will be returned. Also some static property like id, name, create_time
        and unexisted properties should NOT be updated.

        :type args: dict
        :param args: arguments from http body which contains new values

        :type hackathon: Hackathon
        :param hackathon: the existing Hackathon object which contains old values

        :rtype: dict
        :return a dict that contains all properties that are updated.
        """
        result = {}

        hackathon_dic = hackathon.dic()
        for key in dict(args):
            if hackathon_dic.has_key(key) and dict(args)[key] != hackathon_dic[key]:
                result[key] = dict(args)[key]

        result.pop('id', None)
        result.pop('name', None)
        result.pop('creator', None)
        result.pop('create_time', None)
        result['update_time'] = self.util.get_now()
        return result

    def __get_hackathon_stat(self, hackathon):
        stats = HackathonStat.objects(hackathon=hackathon).all()
        result = {
            "hackathon_id": str(hackathon.id),
            "online": 0,
            "offline": 0
        }
        for item in stats:
            result[item.type] = item.count

        reg_list = UserHackathon.objects(hackathon=hackathon,
                                         role=HACK_USER_TYPE.COMPETITOR,
                                         deleted=False,
                                         status__in=[HACK_USER_STATUS.AUTO_PASSED, HACK_USER_STATUS.AUDIT_PASSED]
                                         ).only("user").no_dereference().all()
        reg_list = [uh.user.id for uh in reg_list]
        reg_count = len(reg_list)
        if reg_count > 0:
            online_count = User.objects(id__in=reg_list, online=True).count()
            result["online"] = online_count
            result["offline"] = reg_count - online_count

        return result

    def __get_config_cache_key(self, hackathon):
        return "hackathon_config_%s" % hackathon.id

    def __create_default_data_for_local(self, hackathon):
        """
        create test data for new hackathon. It's for local development only
        :param hackathon:
        :return:
        """
        try:
            # test docker host server
            host = DockerHostServer(vm_name="localhost",
                                    public_dns="localhost",
                                    public_ip="127.0.0.1",
                                    public_docker_api_port=4243,
                                    private_ip="127.0.0.1",
                                    private_docker_api_port=4243,
                                    container_count=0,
                                    container_max_count=100,
                                    disabled=False,
                                    state=DockerHostServerStatus.DOCKER_READY,
                                    hackathon=hackathon)
            host.save()
        except Exception as e:
            self.log.error(e)
            self.log.warn("fail to create test data")

    def __validate_upload_files(self):
        # check file size
        if request.content_length > len(request.files) * self.util.get_config("storage.size_limit_kilo_bytes") * 1024:
            raise BadRequest("more than the file size limited")

        # check each file type
        for file_name in request.files:
            if request.files.get(file_name).filename.endswith('jpg'):
                continue  # jpg is not considered in imghdr
            if imghdr.what(request.files.get(file_name)) is None:
                raise BadRequest("only images can be uploaded")


'''
Attach extension methods to Hackathon entity so that we can code like 'if hackathon.is_auto_approve(): ....' where
hackathon is entity of Hackathon that defines in database/models.py.
'''


def is_auto_approve(hackathon):
    hack_manager = RequiredFeature("hackathon_manager")
    value = hack_manager.get_basic_property(hackathon, HACKATHON_CONFIG.AUTO_APPROVE, "1")
    return util.str2bool(value)


def is_pre_allocate_enabled(hackathon):
    if hackathon.status != HACK_STATUS.ONLINE:
        return False

    if hackathon.event_end_time < util.get_now():
        return False

    hack_manager = RequiredFeature("hackathon_manager")
    value = hack_manager.get_basic_property(hackathon, HACKATHON_CONFIG.PRE_ALLOCATE_ENABLED, "1")
    return util.str2bool(value)


def get_pre_allocate_number(hackathon):
    hack_manager = RequiredFeature("hackathon_manager")
    value = hack_manager.get_basic_property(hackathon, HACKATHON_CONFIG.PRE_ALLOCATE_NUMBER, 1)
    return int(value)

def get_cloud_provider(hackathon):
    hack_manager = RequiredFeature("hackathon_manager")
    value = hack_manager.get_basic_property(hackathon, HACKATHON_CONFIG.CLOUD_PROVIDER, 0)
    return int(value)


def get_basic_property(hackathon, property_name, default_value=None):
    hack_manager = RequiredFeature("hackathon_manager")
    return hack_manager.get_basic_property(hackathon, property_name, default_value)


Hackathon.is_auto_approve = is_auto_approve
Hackathon.is_pre_allocate_enabled = is_pre_allocate_enabled
Hackathon.get_pre_allocate_number = get_pre_allocate_number
Hackathon.get_basic_property = get_basic_property
