# (c) Copyright 2013, 2014, University of Manchester
#
# HydraPlatform is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HydraPlatform is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HydraPlatform.  If not, see <http://www.gnu.org/licenses/>
#

from sqlalchemy import Column,\
ForeignKey,\
text,\
Integer,\
String,\
LargeBinary,\
TIMESTAMP,\
BIGINT,\
Float,\
Text

from sqlalchemy import inspect

from HydraLib.HydraException import HydraError, PermissionError

from sqlalchemy.orm import relationship, backref

from HydraLib.hydra_dateutil import ordinal_to_timestamp, get_datetime

from HydraServer.db import DeclarativeBase as Base, DBSession

from HydraServer.util import generate_data_hash, get_val

from sqlalchemy.sql.expression import case
from sqlalchemy import UniqueConstraint, and_

import pandas as pd

import json
import zlib
from HydraLib import config

import logging
import bcrypt
log = logging.getLogger(__name__)

def get_timestamp(ordinal):
    """
        Turn an ordinal timestamp into a datetime string.
    """
    if ordinal is None:
        return None
    timestamp = str(ordinal_to_timestamp(ordinal))
    return timestamp


#***************************************************
#Data
#***************************************************

class Inspect(object):
    def get_columns_and_relationships(self):
        return inspect(self).attrs.keys()

class Dataset(Base, Inspect):
    """
        Table holding all the attribute values
    """
    __tablename__='tDataset'

    dataset_id = Column(Integer(), primary_key=True, index=True, nullable=False)
    data_type = Column(String(60),  nullable=False)
    data_units = Column(String(60))
    data_dimen = Column(String(60), server_default='dimensionless')
    data_name = Column(String(120),  nullable=False)
    data_hash = Column(BIGINT(),  nullable=False, unique=True)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    created_by = Column(Integer(), ForeignKey('tUser.user_id'))
    hidden = Column(String(1),  nullable=False, server_default=text(u"'N'"))

    start_time = Column(String(60),  nullable=True)
    frequency = Column(String(10),  nullable=True)
    value = Column('value', LargeBinary(),  nullable=True)

    user = relationship('User', backref=backref("datasets", order_by=dataset_id))

    def set_metadata(self, metadata_dict):
        """
            Set the metadata on a dataset

            **metadata_dict**: A dictionary of metadata key-vals.
            Transforms this dict into an array of metadata objects for
            storage in the DB.
        """
        if metadata_dict is None:
            return
        existing_metadata = []
        for m in self.metadata:
            existing_metadata.append(m.metadata_name)
            if m.metadata_name in metadata_dict:
                if m.metadata_val != metadata_dict[m.metadata_name]:
                    m.metadata_val = metadata_dict[m.metadata_name]

        for k, v in metadata_dict.items():
            if k not in existing_metadata:
                m_i = Metadata(metadata_name=str(k),metadata_val=str(v))
                self.metadata.append(m_i)

    def get_val(self, timestamp=None):
        """
            If a timestamp is passed to this function,
            return the values appropriate to the requested times.

            If the timestamp is *before* the start of the timeseries data, return None
            If the timestamp is *after* the end of the timeseries data, return the last
            value.

            The raw flag indicates whether timeseries should be returned raw -- exactly
            as they are in the DB (a timeseries being a list of timeseries data objects,
            for example) or as a single python dictionary
        """
        val = get_val(self, timestamp)
        return val

    def set_val(self, data_type, val):
        if data_type in ('descriptor','scalar'):
            self.value = str(val)
        elif data_type == 'array':
            if type(val) != str:
                val = json.dumps(val)

            if len(val) > config.get('db', 'compression_threshold', 5000):
                self.value = zlib.compress(val)
            else:
                self.value = val
        elif data_type == 'timeseries':
            if type(val) == list:
                test_val_keys = []
                test_vals = []
                for time, value in val:
                    try:
                        v = eval(value)
                    except:
                        v = value
                    try:
                        test_val_keys.append(get_datetime(time))
                    except:
                        test_val_keys.append(time)
                    test_vals.append(v)

                timeseries_pd = pd.DataFrame(test_vals, index=pd.Series(test_val_keys))
                #Epoch doesn't work here because dates before 1970 are not supported
                #in read_json. Ridiculous.
                json_value =  timeseries_pd.to_json(date_format='iso', date_unit='ns')
                if len(json_value) > config.get('db', 'compression_threshold', 5000):
                    self.value = zlib.compress(json_value)
                else:
                    self.value = json_value
            else:
                self.value = val
        else:
            raise HydraError("Invalid data type %s"%(data_type,))

    def set_hash(self,metadata=None):


        if metadata is None:
            metadata = self.get_metadata_as_dict()

        dataset_dict = dict(data_name = self.data_name,
                           data_units = self.data_units,
                           data_dimen = self.data_dimen,
                           data_type  = self.data_type,
                           value      = self.value,
                           metadata   = metadata)

        data_hash = generate_data_hash(dataset_dict)

        self.data_hash = data_hash

        return data_hash

    def get_metadata_as_dict(self):
        metadata = {}
        for r in self.metadata:
            val = str(r.metadata_val)

            metadata[str(r.metadata_name)] = val

        return metadata

    def set_owner(self, user_id, read='Y', write='Y', share='Y'):
        owner = None
        for o in self.owners:
            if user_id == o.user_id:
                owner = o
                break
        else:
            owner = DatasetOwner()
            owner.dataset_id = self.dataset_id
            owner.user_id = int(user_id)
            self.owners.append(owner)

        owner.view  = read
        owner.edit  = write
        owner.share = share
        return owner

    def unset_owner(self, user_id):
        owner = None
        if str(user_id) == str(self.created_by):
            log.warn("Cannot unset %s as owner, as they created the dataset", user_id)
            return
        for o in self.owners:
            if user_id == o.user_id:
                owner = o
                DBSession.delete(owner)
                break

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this dataset
        """
        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if int(owner.user_id) == int(user_id):
                if owner.view == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have read"
                             " access on dataset %s" %
                             (user_id, self.dataset_id))

    def check_user(self, user_id):
        """
            Check whether this user can read this dataset
        """

        if self.hidden == 'N':
            return True

        for owner in self.owners:
            if int(owner.user_id) == int(user_id):
                if owner.view == 'Y':
                    return True
        return False

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this dataset
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.edit == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have edit"
                             " access on dataset %s" %
                             (user_id, self.dataset_id))

    def check_share_permission(self, user_id):
        """
            Check whether this user can write this dataset
        """
        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.share == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have share"
                             " access on dataset %s" %
                             (user_id, self.dataset_id))

class DatasetCollection(Base, Inspect):
    """
    """

    __tablename__='tDatasetCollection'

    collection_id = Column(Integer(), primary_key=True, nullable=False)
    collection_name = Column(String(60),  nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

class DatasetCollectionItem(Base, Inspect):
    """
    """

    __tablename__='tDatasetCollectionItem'

    collection_id = Column(Integer(), ForeignKey('tDatasetCollection.collection_id'), primary_key=True, nullable=False)
    dataset_id = Column(Integer(), ForeignKey('tDataset.dataset_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    collection = relationship('DatasetCollection', backref=backref("items", order_by=dataset_id, cascade="all, delete-orphan"))
    dataset = relationship('Dataset', backref=backref("collectionitems", order_by=dataset_id,  cascade="all, delete-orphan"))

class Metadata(Base, Inspect):
    """
    """

    __tablename__='tMetadata'

    dataset_id = Column(Integer(), ForeignKey('tDataset.dataset_id'), primary_key=True, nullable=False, index=True)
    metadata_name = Column(String(60), primary_key=True, nullable=False)
    metadata_val = Column(LargeBinary(),  nullable=False)

    dataset = relationship('Dataset', backref=backref("metadata", order_by=dataset_id, cascade="all, delete-orphan"))



#********************************************************
#Attributes & Templates
#********************************************************

class Attr(Base, Inspect):
    """
    """

    __tablename__='tAttr'

    __table_args__ = (
        UniqueConstraint('attr_name', 'attr_dimen', name="unique name dimension"),
    )

    attr_id           = Column(Integer(), primary_key=True, nullable=False)
    attr_name         = Column(String(60),  nullable=False)
    attr_dimen        = Column(String(60), server_default=text(u"'dimensionless'"))
    attr_description  = Column(String(1000))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

class AttrMap(Base, Inspect):
    """
    """

    __tablename__='tAttrMap'

    attr_id_a = Column(Integer(), ForeignKey('tAttr.attr_id'), primary_key=True, nullable=False)
    attr_id_b = Column(Integer(), ForeignKey('tAttr.attr_id'), primary_key=True, nullable=False)

    attr_a = relationship("Attr", foreign_keys=[attr_id_a], backref=backref('maps_to', order_by=attr_id_a))
    attr_b = relationship("Attr", foreign_keys=[attr_id_b], backref=backref('maps_from', order_by=attr_id_b))


class ResourceAttrMap(Base, Inspect):
    """
    """

    __tablename__='tResourceAttrMap'

    network_a_id       = Column(Integer(), ForeignKey('tNetwork.network_id'), primary_key=True, nullable=False)
    network_b_id       = Column(Integer(), ForeignKey('tNetwork.network_id'), primary_key=True, nullable=False)
    resource_attr_id_a = Column(Integer(), ForeignKey('tResourceAttr.resource_attr_id'), primary_key=True, nullable=False)
    resource_attr_id_b = Column(Integer(), ForeignKey('tResourceAttr.resource_attr_id'), primary_key=True, nullable=False)

    resourceattr_a = relationship("ResourceAttr", foreign_keys=[resource_attr_id_a])
    resourceattr_b = relationship("ResourceAttr", foreign_keys=[resource_attr_id_b])

    network_a = relationship("Network", foreign_keys=[network_a_id])
    network_b = relationship("Network", foreign_keys=[network_b_id])

class Template(Base, Inspect):
    """
    """

    __tablename__='tTemplate'

    template_id = Column(Integer(), primary_key=True, nullable=False)
    template_name = Column(String(60),  nullable=False, unique=True)
    cr_date = Column(TIMESTAMP(), nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    created_by = Column(Integer(), ForeignKey('tUser.user_id'))
    layout = Column(Text(1000))

    def set_owner(self, user_id, read='Y', write='Y', share='Y'):
        owner = None
        for o in self.owners:
            if str(user_id) == str(o.user_id):
                owner = o
                break
        else:
            owner = TemplateOwner()
            owner.template_id = self.template_id
            self.owners.append(owner)

        owner.user_id = int(user_id)
        owner.view  = read
        owner.edit  = write
        owner.share = share

        return owner

    def unset_owner(self, user_id):
        owner = None
        if str(user_id) == str(self.created_by):
            log.warn("Cannot unset %s as owner, as they created the template", user_id)
            return
        for o in self.owners:
            if user_id == o.user_id:
                owner = o
                DBSession.delete(owner)
                break

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this template
        """

        for owner in self.owners:
            if int(owner.user_id) == int(user_id) or int(owner.user_id) == 1:
                if owner.view == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have read"
                             " access on template %s" %
                             (user_id, self.template_id))

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this project
        """

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.edit == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have edit"
                             " access on template %s" %
                             (user_id, self.network_id))

    def check_share_permission(self, user_id):
        """
            Check whether this user can write this template
        """

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.share == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have share"
                             " access on template %s" %
                             (user_id, self.template))


class TemplateType(Base, Inspect):
    """
    """

    __tablename__='tTemplateType'
    __table_args__ = (
        UniqueConstraint('template_id', 'type_name', 'resource_type', name="unique type name"),
    )

    type_id = Column(Integer(), primary_key=True, nullable=False)
    type_name = Column(String(60),  nullable=False)
    template_id = Column(Integer(), ForeignKey('tTemplate.template_id'), nullable=False)
    resource_type = Column(String(60))
    alias = Column(String(100))
    layout = Column(Text(1000))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    template = relationship('Template', backref=backref("templatetypes", order_by=type_id, cascade="all, delete-orphan"))



class TypeAttr(Base, Inspect):
    """
    """

    __tablename__='tTypeAttr'

    attr_id = Column(Integer(), ForeignKey('tAttr.attr_id'), primary_key=True, nullable=False)
    type_id = Column(Integer(), ForeignKey('tTemplateType.type_id', ondelete='CASCADE'), primary_key=True, nullable=False)
    default_dataset_id = Column(Integer(), ForeignKey('tDataset.dataset_id'))
    attr_is_var        = Column(String(1), server_default=text(u"'N'"))
    data_type          = Column(String(60))
    data_restriction   = Column(Text(1000))
    unit               = Column(String(60))
    description        = Column(Text(1000))
    properties         = Column(Text(1000))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    attr = relationship('Attr')
    templatetype = relationship('TemplateType',  backref=backref("typeattrs", order_by=attr_id, cascade="all, delete-orphan"))
    default_dataset = relationship('Dataset')

    def get_attr(self):

        if self.attr is None:
            attr = DBSession.query(Attr).filter(Attr.attr_id==self.attr_id).first()
        else:
            attr = self.attr

        return attr


class ResourceAttr(Base, Inspect):
    """
    """

    __tablename__='tResourceAttr'

    __table_args__ = (
        UniqueConstraint('network_id', 'attr_id', name = 'net_attr_1'),
        UniqueConstraint('project_id', 'attr_id', name = 'proj_attr_1'),
        UniqueConstraint('node_id',    'attr_id', name = 'node_attr_1'),
        UniqueConstraint('link_id',    'attr_id', name = 'link_attr_1'),
        UniqueConstraint('group_id',   'attr_id', name = 'group_attr_1'),
    )

    resource_attr_id = Column(Integer(), primary_key=True, nullable=False)
    attr_id = Column(Integer(), ForeignKey('tAttr.attr_id'),  nullable=False)
    ref_key = Column(String(60),  nullable=False, index=True)
    network_id  = Column(Integer(),  ForeignKey('tNetwork.network_id'), index=True, nullable=True,)
    project_id  = Column(Integer(),  ForeignKey('tProject.project_id'), index=True, nullable=True,)
    node_id     = Column(Integer(),  ForeignKey('tNode.node_id'), index=True, nullable=True)
    link_id     = Column(Integer(),  ForeignKey('tLink.link_id'), index=True, nullable=True)
    group_id    = Column(Integer(),  ForeignKey('tResourceGroup.group_id'), index=True, nullable=True)
    attr_is_var = Column(String(1),  nullable=False, server_default=text(u"'N'"))
    unit        = Column(String(60),  nullable=True, server_default=text(u"''"))
    data_type   = Column(String(60),  nullable=True, server_default=text(u"''"))
    description = Column(String(1000),  nullable=True, server_default=text(u"''"))
    properties  = Column(String(1000),  nullable=True, server_default=text(u"'{}'"))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    attr = relationship('Attr')
    project = relationship('Project', backref=backref('attributes', uselist=True, cascade="all, delete-orphan"), uselist=False)
    network = relationship('Network', backref=backref('attributes', uselist=True, cascade="all, delete-orphan"), uselist=False)
    node = relationship('Node', backref=backref('attributes', uselist=True, cascade="all, delete-orphan"), uselist=False)
    link = relationship('Link', backref=backref('attributes', uselist=True, cascade="all, delete-orphan"), uselist=False)
    resourcegroup = relationship('ResourceGroup', backref=backref('attributes', uselist=True, cascade="all, delete-orphan"), uselist=False)


    def get_network(self):
        """
         Get the network that this resource attribute is in.
        """
        ref_key = self.ref_key
        if ref_key == 'NETWORK':
            return self.network
        elif ref_key == 'NODE':
            return self.node.network
        elif ref_key == 'LINK':
            return self.link.network
        elif ref_key == 'GROUP':
            return self.group.network
        elif ref_key == 'PROJECT':
            return None

    def get_resource(self):
        ref_key = self.ref_key
        if ref_key == 'NETWORK':
            return self.network
        elif ref_key == 'NODE':
            return self.node
        elif ref_key == 'LINK':
            return self.link
        elif ref_key == 'GROUP':
            return self.resourcegroup
        elif ref_key == 'PROJECT':
            return self.project

    def get_resource_id(self):
        ref_key = self.ref_key
        if ref_key == 'NETWORK':
            return self.network_id
        elif ref_key == 'NODE':
            return self.node_id
        elif ref_key == 'LINK':
            return self.link_id
        elif ref_key == 'GROUP':
            return self.group_id
        elif ref_key == 'PROJECT':
            return self.project_id

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this resource attribute
        """
        self.get_resource().check_read_permission(user_id)

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this node
        """
        self.get_resource().check_write_permission(user_id)


class ResourceType(Base, Inspect):
    """
    """

    __tablename__='tResourceType'
    __table_args__ = (
        UniqueConstraint('network_id', 'type_id', name='net_type_1'),
        UniqueConstraint('node_id', 'type_id', name='node_type_1'),
        UniqueConstraint('link_id', 'type_id',  name = 'link_type_1'),
        UniqueConstraint('group_id', 'type_id', name = 'group_type_1'),

    )
    resource_type_id = Column(Integer, primary_key=True, nullable=False)
    type_id = Column(Integer(), ForeignKey('tTemplateType.type_id'), primary_key=False, nullable=False)
    ref_key = Column(String(60),nullable=False)
    network_id  = Column(Integer(),  ForeignKey('tNetwork.network_id'), nullable=True,)
    node_id     = Column(Integer(),  ForeignKey('tNode.node_id'), nullable=True)
    link_id     = Column(Integer(),  ForeignKey('tLink.link_id'), nullable=True)
    group_id    = Column(Integer(),  ForeignKey('tResourceGroup.group_id'), nullable=True)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))


    templatetype = relationship('TemplateType', backref=backref('resourcetypes', uselist=True, cascade="all, delete-orphan"))

    network = relationship('Network', backref=backref('types', uselist=True, cascade="all, delete-orphan"), uselist=False)
    node = relationship('Node', backref=backref('types', uselist=True, cascade="all, delete-orphan"), uselist=False)
    link = relationship('Link', backref=backref('types', uselist=True, cascade="all, delete-orphan"), uselist=False)
    resourcegroup = relationship('ResourceGroup', backref=backref('types', uselist=True, cascade="all, delete-orphan"), uselist=False)

    def get_resource(self):
        ref_key = self.ref_key
        if ref_key == 'PROJECT':
            return self.project
        elif ref_key == 'NETWORK':
            return self.network
        elif ref_key == 'NODE':
            return self.node
        elif ref_key == 'LINK':
            return self.link
        elif ref_key == 'GROUP':
            return self.group

    def get_resource_id(self):
        ref_key = self.ref_key
        if ref_key == 'PROJECT':
            return self.project_id
        elif ref_key == 'NETWORK':
            return self.network_id
        elif ref_key == 'NODE':
            return self.node_id
        elif ref_key == 'LINK':
            return self.link_id
        elif ref_key == 'GROUP':
            return self.group_id

#*****************************************************
# Topology & Scenarios
#*****************************************************

class Project(Base, Inspect):
    """
    """

    __tablename__='tProject'
    ref_key = 'PROJECT'


    __table_args__ = (
        UniqueConstraint('project_name', 'created_by', 'status', name="unique proj name"),
    )

    attribute_data = []

    project_id = Column(Integer(), primary_key=True, nullable=False)
    project_name = Column(String(60),  nullable=False, unique=False)
    project_description = Column(String(1000))
    layout = Column(Text(2000), server_default=text(u'{}'))
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    created_by = Column(Integer(), ForeignKey('tUser.user_id'))

    user = relationship('User', backref=backref("projects", order_by=project_id))

    def get_name(self):
        return self.project_name

    def get_attribute_data(self):
        attribute_data_rs = DBSession.query(ResourceScenario).join(ResourceAttr).filter(ResourceAttr.project_id==1).all()
        self.attribute_data = attribute_data_rs
        return attribute_data_rs

    def add_attribute(self, attr_id, attr_is_var='N'):
        attr = ResourceAttr()
        attr.attr_id = attr_id
        attr.attr_is_var = attr_is_var
        attr.ref_key = self.ref_key
        attr.project_id  = self.project_id
        self.attributes.append(attr)

        return attr

    def set_owner(self, user_id, read='Y', write='Y', share='Y'):
        owner = None
        for o in self.owners:
            if str(user_id) == str(o.user_id):
                owner = o
                break
        else:
            owner = ProjectOwner()
            owner.project_id = self.project_id
            owner.user_id = int(user_id)
            self.owners.append(owner)

        owner.view  = read
        owner.edit  = write
        owner.share = share

        return owner

    def unset_owner(self, user_id):
        owner = None
        if str(user_id) == str(self.created_by):
            log.warn("Cannot unset %s as owner, as they created the project", user_id)
            return
        for o in self.owners:
            if user_id == o.user_id:
                owner = o
                DBSession.delete(owner)
                break

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this project
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if int(owner.user_id) == int(user_id):
                if owner.view == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have read"
                             " access on project %s" %
                             (user_id, self.project_id))

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this project
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.edit == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have edit"
                             " access on project %s" %
                             (user_id, self.project_id))

    def check_share_permission(self, user_id):
        """
            Check whether this user can write this project
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.share == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have share"
                             " access on project %s" %
                             (user_id, self.project_id))



class Network(Base, Inspect):
    """
    """

    __tablename__='tNetwork'
    __table_args__ = (
        UniqueConstraint('network_name', 'project_id', name="unique net name"),
    )
    ref_key = 'NETWORK'

    network_id = Column(Integer(), primary_key=True, nullable=False)
    network_name = Column(String(60),  nullable=False)
    network_description = Column(String(1000))
    layout = Column(Text(1000))
    project_id = Column(Integer(), ForeignKey('tProject.project_id'),  nullable=False)
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    projection = Column(String(1000))
    created_by = Column(Integer(), ForeignKey('tUser.user_id'))

    project = relationship('Project', backref=backref("networks", order_by=network_id, cascade="all, delete-orphan"))

    def get_name(self):
        return self.network_name

    def add_attribute(self, attr_id, attr_is_var='N'):
        attr = ResourceAttr()
        attr.attr_id = attr_id
        attr.attr_is_var = attr_is_var
        attr.ref_key = self.ref_key
        attr.network_id  = self.network_id
        self.attributes.append(attr)

        return attr

    def add_link(self, name, desc, layout, node_1, node_2):
        """
            Add a link to a network. Links are what effectively
            define the network topology, by associating two already
            existing nodes.
        """

        existing_link = DBSession.query(Link).filter(Link.link_name==name, Link.network_id==self.network_id).first()
        if existing_link is not None:
            raise HydraError("A link with name %s is already in network %s"%(name, self.network_id))

        l = Link()
        l.link_name        = name
        l.link_description = desc
        l.layout           = str(layout) if layout is not None else None
        l.node_a           = node_1
        l.node_b           = node_2

        DBSession.add(l)

        self.links.append(l)

        return l


    def add_node(self, name, desc, layout, node_x, node_y):
        """
            Add a node to a network.
        """

        existing_node = DBSession.query(Node).filter(Node.node_name==name, Node.network_id==self.network_id).first()
        if existing_node is not None:
            raise HydraError("A node with name %s is already in network %s"%(name, self.network_id))

        node = Node()
        node.node_name        = name
        node.node_description = desc
        node.layout           = str(layout) if layout is not None else None
        node.node_x           = node_x
        node.node_y           = node_y

        #Do not call save here because it is likely that we may want
        #to bulk insert nodes, not one at a time.

        DBSession.add(node)

        self.nodes.append(node)

        return node

    def add_group(self, name, desc, status):
        """
            Add a new group to a network.
        """

        existing_group = DBSession.query(ResourceGroup).filter(ResourceGroup.group_name==name, ResourceGroup.network_id==self.network_id).first()
        if existing_group is not None:
            raise HydraError("A resource group with name %s is already in network %s"%(name, self.network_id))

        group_i                      = ResourceGroup()
        group_i.group_name        = name
        group_i.group_description = desc
        group_i.status            = status

        DBSession.add(group_i)

        self.resourcegroups.append(group_i)


        return group_i

    def set_owner(self, user_id, read='Y', write='Y', share='Y'):
        owner = None
        for o in self.owners:
            if str(user_id) == str(o.user_id):
                owner = o
                break
        else:
            owner = NetworkOwner()
            owner.network_id = self.network_id
            self.owners.append(owner)

        owner.user_id = int(user_id)
        owner.view  = read
        owner.edit  = write
        owner.share = share

        return owner

    def unset_owner(self, user_id):
        owner = None
        if str(user_id) == str(self.created_by):
            log.warn("Cannot unset %s as owner, as they created the network", user_id)
            return
        for o in self.owners:
            if user_id == o.user_id:
                owner = o
                DBSession.delete(owner)
                break

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this network
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if int(owner.user_id) == int(user_id):
                if owner.view == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have read"
                             " access on network %s" %
                             (user_id, self.network_id))

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this project
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.edit == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have edit"
                             " access on network %s" %
                             (user_id, self.network_id))

    def check_share_permission(self, user_id):
        """
            Check whether this user can write this project
        """

        if self.created_by == int(user_id):
            return

        for owner in self.owners:
            if owner.user_id == int(user_id):
                if owner.view == 'Y' and owner.share == 'Y':
                    break
        else:
            raise PermissionError("Permission denied. User %s does not have share"
                             " access on network %s" %
                             (user_id, self.network_id))

class Link(Base, Inspect):
    """
    """

    __tablename__='tLink'

    __table_args__ = (
        UniqueConstraint('network_id', 'link_name', name="unique link name"),
    )
    ref_key = 'LINK'

    link_id = Column(Integer(), primary_key=True, nullable=False)
    network_id = Column(Integer(), ForeignKey('tNetwork.network_id'), nullable=False)
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    node_1_id = Column(Integer(), ForeignKey('tNode.node_id'), nullable=False)
    node_2_id = Column(Integer(), ForeignKey('tNode.node_id'), nullable=False)
    link_name = Column(String(120))
    link_description = Column(String(1000))
    layout = Column(Text(1000))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    network = relationship('Network', backref=backref("links", order_by=network_id, cascade="all, delete-orphan"), lazy='joined')
    node_a = relationship('Node', foreign_keys=[node_1_id], backref=backref("links_to", order_by=link_id, cascade="all, delete-orphan"))
    node_b = relationship('Node', foreign_keys=[node_2_id], backref=backref("links_from", order_by=link_id, cascade="all, delete-orphan"))

    def get_name(self):
        return self.link_name

    def add_attribute(self, attr_id, attr_is_var='N'):
        attr = ResourceAttr()
        attr.attr_id = attr_id
        attr.attr_is_var = attr_is_var
        attr.ref_key = self.ref_key
        attr.link_id  = self.link_id
        self.attributes.append(attr)

        return attr

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this link
        """
        self.network.check_read_permission(user_id)

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this link
        """

        self.network.check_write_permission(user_id)

class Node(Base, Inspect):
    """
    """

    __tablename__='tNode'
    __table_args__ = (
        UniqueConstraint('network_id', 'node_name', name="unique node name"),
    )
    ref_key = 'NODE'

    node_id = Column(Integer(), primary_key=True, nullable=False)
    network_id = Column(Integer(), ForeignKey('tNetwork.network_id'), nullable=False)
    node_description = Column(String(1000))
    node_name = Column(String(120),  nullable=False)
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    node_x = Column(Float(precision=10, asdecimal=True))
    node_y = Column(Float(precision=10, asdecimal=True))
    layout = Column(Text(1000))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    network = relationship('Network', backref=backref("nodes", order_by=network_id, cascade="all, delete-orphan"), lazy='joined')

    def get_name(self):
        return self.node_name

    def add_attribute(self, attr_id, attr_is_var='N'):
        attr = ResourceAttr()
        attr.attr_id = attr_id
        attr.attr_is_var = attr_is_var
        attr.ref_key = self.ref_key
        attr.node_id  = self.node_id
        self.attributes.append(attr)

        return attr

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this node
        """
        self.network.check_read_permission(user_id)

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this node
        """

        self.network.check_write_permission(user_id)

class ResourceGroup(Base, Inspect):
    """
    """

    __tablename__='tResourceGroup'
    __table_args__ = (
        UniqueConstraint('network_id', 'group_name', name="unique resourcegroup name"),
    )

    ref_key = 'GROUP'
    group_id = Column(Integer(), primary_key=True, nullable=False)
    group_name = Column(String(60),  nullable=False)
    group_description = Column(String(1000))
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    network_id = Column(Integer(), ForeignKey('tNetwork.network_id'),  nullable=False)

    network = relationship('Network', backref=backref("resourcegroups", order_by=group_id, cascade="all, delete-orphan"), lazy='joined')

    def get_name(self):
        return self.group_name

    def add_attribute(self, attr_id, attr_is_var='N'):
        attr = ResourceAttr()
        attr.attr_id = attr_id
        attr.attr_is_var = attr_is_var
        attr.ref_key = self.ref_key
        attr.group_id  = self.group_id
        self.attributes.append(attr)

        return attr

    def get_items(self, scenario_id):
        """
            Get all the items in this group, in the given scenario
        """
        items = DBSession.query(ResourceGroupItem)\
                .filter(ResourceGroupItem.group_id==self.group_id).\
                filter(ResourceGroupItem.scenario_id==scenario_id).all()

        return items

    def check_read_permission(self, user_id):
        """
            Check whether this user can read this group
        """
        self.network.check_read_permission(user_id)

    def check_write_permission(self, user_id):
        """
            Check whether this user can write this group
        """

        self.network.check_write_permission(user_id)

class ResourceGroupItem(Base, Inspect):
    """
    """

    __tablename__='tResourceGroupItem'

    __table_args__ = (
        UniqueConstraint('group_id', 'node_id', 'scenario_id', name='node_group_1'),
        UniqueConstraint('group_id', 'link_id', 'scenario_id',  name = 'link_group_1'),
        UniqueConstraint('group_id', 'subgroup_id', 'scenario_id', name = 'subgroup_group_1'),
    )

    item_id = Column(Integer(), primary_key=True, nullable=False)
    ref_key = Column(String(60),  nullable=False)

    node_id     = Column(Integer(),  ForeignKey('tNode.node_id'))
    link_id     = Column(Integer(),  ForeignKey('tLink.link_id'))
    subgroup_id = Column(Integer(),  ForeignKey('tResourceGroup.group_id'))

    group_id = Column(Integer(), ForeignKey('tResourceGroup.group_id'))
    scenario_id = Column(Integer(), ForeignKey('tScenario.scenario_id'),  nullable=False, index=True)

    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    group = relationship('ResourceGroup', foreign_keys=[group_id], backref=backref("items", order_by=group_id))
    scenario = relationship('Scenario', backref=backref("resourcegroupitems", order_by=item_id, cascade="all, delete-orphan"))

    #These need to have backrefs to allow the deletion of networks & projects
    #--There needs to be a connection between the items & the resources to allow it
    node = relationship('Node', backref=backref("resourcegroupitems", order_by=item_id, cascade="all, delete-orphan"))
    link = relationship('Link', backref=backref("resourcegroupitems", order_by=item_id, cascade="all, delete-orphan"))
    subgroup = relationship('ResourceGroup', foreign_keys=[subgroup_id])


    def get_resource(self):
        ref_key = self.ref_key
        if ref_key == 'NODE':
            return self.node
        elif ref_key == 'LINK':
            return self.link
        elif ref_key == 'GROUP':
            return self.subgroup

    def get_resource_id(self):
        ref_key = self.ref_key
        if ref_key == 'NODE':
            return self.node_id
        elif ref_key == 'LINK':
            return self.link_id
        elif ref_key == 'GROUP':
            return self.subgroup_id

class ResourceScenario(Base, Inspect):
    """
    """

    __tablename__='tResourceScenario'

    dataset_id = Column(Integer(), ForeignKey('tDataset.dataset_id'), nullable=False)
    scenario_id = Column(Integer(), ForeignKey('tScenario.scenario_id'), primary_key=True, nullable=False, index=True)
    resource_attr_id = Column(Integer(), ForeignKey('tResourceAttr.resource_attr_id'), primary_key=True, nullable=False, index=True)
    source           = Column(String(60))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    dataset      = relationship('Dataset', backref=backref("resourcescenarios", order_by=dataset_id))
    scenario     = relationship('Scenario', backref=backref("resourcescenarios", order_by=resource_attr_id, cascade="all, delete-orphan"))
    resourceattr = relationship('ResourceAttr', backref=backref("resourcescenarios", cascade="all, delete-orphan"), uselist=False)

    def get_dataset(self, user_id):
        dataset = DBSession.query(Dataset.dataset_id,
                Dataset.data_type,
                Dataset.data_units,
                Dataset.data_dimen,
                Dataset.data_name,
                Dataset.hidden,
                case([(and_(Dataset.hidden=='Y', DatasetOwner.user_id is not None), None)],
                        else_=Dataset.start_time).label('start_time'),
                case([(and_(Dataset.hidden=='Y', DatasetOwner.user_id is not None), None)],
                        else_=Dataset.frequency).label('frequency'),
                case([(and_(Dataset.hidden=='Y', DatasetOwner.user_id is not None), None)],
                        else_=Dataset.value).label('value')).filter(
                Dataset.dataset_id==self.dataset_id).outerjoin(DatasetOwner,
                                    and_(Dataset.dataset_id==DatasetOwner.dataset_id,
                                    DatasetOwner.user_id==user_id)).one()

        return dataset


class Scenario(Base, Inspect):
    """
    """

    __tablename__='tScenario'
    __table_args__ = (
        UniqueConstraint('network_id', 'scenario_name', name="unique scenario name"),
    )

    scenario_id = Column(Integer(), primary_key=True, index=True, nullable=False)
    scenario_name = Column(String(60),  nullable=False)
    scenario_description = Column(String(1000))
    layout = Column(Text(1000))
    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    network_id = Column(Integer(), ForeignKey('tNetwork.network_id'), index=True)
    start_time = Column(String(60))
    end_time = Column(String(60))
    locked = Column(String(1),  nullable=False, server_default=text(u"'N'"))
    time_step = Column(String(60))
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    created_by = Column(Integer(), ForeignKey('tUser.user_id'))

    network = relationship('Network', backref=backref("scenarios", order_by=scenario_id))

    def add_resource_scenario(self, resource_attr, dataset=None, source=None):
        rs_i = ResourceScenario()
        if resource_attr.resource_attr_id is None:
            rs_i.resourceattr = resource_attr
        else:
            rs_i.resource_attr_id = resource_attr.resource_attr_id

        if dataset.dataset_id is None:
            rs_i.dataset = dataset
        else:
            rs_i.dataset_id = dataset.dataset_id
        rs_i.source = source
        self.resourcescenarios.append(rs_i)

    def add_resourcegroup_item(self, ref_key, resource, group_id):
        group_item_i = ResourceGroupItem()
        group_item_i.group_id = group_id
        group_item_i.ref_key  = ref_key
        if ref_key == 'GROUP':
            group_item_i.subgroup = resource
        elif ref_key == 'NODE':
            group_item_i.node     = resource
        elif ref_key == 'LINK':
            group_item_i.link     = resource
        self.resourcegroupitems.append(group_item_i)

class Rule(Base, Inspect):
    """
        A rule is an arbitrary piece of text applied to resources
        within a scenario. A scenario itself cannot have a rule applied
        to it.
    """

    __tablename__='tRule'
    __table_args__ = (
        UniqueConstraint('scenario_id', 'rule_name', name="unique rule name"),
    )


    rule_id = Column(Integer(), primary_key=True, nullable=False)

    rule_name = Column(String(60), nullable=False)
    rule_description = Column(String(1000), nullable=False)

    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    ref_key = Column(String(60),  nullable=False, index=True)


    rule_text = Column('value', LargeBinary(),  nullable=True)

    status = Column(String(1),  nullable=False, server_default=text(u"'A'"))
    scenario_id = Column(Integer(), ForeignKey('tScenario.scenario_id'),  nullable=False)

    network_id  = Column(Integer(),  ForeignKey('tNetwork.network_id'), index=True, nullable=True,)
    node_id     = Column(Integer(),  ForeignKey('tNode.node_id'), index=True, nullable=True)
    link_id     = Column(Integer(),  ForeignKey('tLink.link_id'), index=True, nullable=True)
    group_id    = Column(Integer(),  ForeignKey('tResourceGroup.group_id'), index=True, nullable=True)

    scenario = relationship('Scenario', backref=backref('rules', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')

class Note(Base, Inspect):
    """
        A note is an arbitrary piece of text which can be applied
        to any resource. A note is NOT scenario dependent. It is applied
        directly to resources. A note can be applied to a scenario.
    """

    __tablename__='tNote'

    note_id = Column(Integer(), primary_key=True, nullable=False)

    ref_key = Column(String(60),  nullable=False, index=True)
    
    #i'd use 'text' here except text is a reserved keyword in sqlalchemy it seems
    note_text    = Column('note_text', LargeBinary(),  nullable=True)

    created_by = Column(Integer(), ForeignKey('tUser.user_id'))

    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    scenario_id = Column(Integer(), ForeignKey('tScenario.scenario_id'),  index=True, nullable=True)
    project_id = Column(Integer(), ForeignKey('tProject.project_id'),  index=True, nullable=True)

    network_id  = Column(Integer(),  ForeignKey('tNetwork.network_id'), index=True, nullable=True,)
    node_id     = Column(Integer(),  ForeignKey('tNode.node_id'), index=True, nullable=True)
    link_id     = Column(Integer(),  ForeignKey('tLink.link_id'), index=True, nullable=True)
    group_id    = Column(Integer(),  ForeignKey('tResourceGroup.group_id'), index=True, nullable=True)

    scenario = relationship('Scenario', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')
    node = relationship('Node', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')
    link = relationship('Link', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')
    group = relationship('ResourceGroup', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')
    network = relationship('Network', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')
    project = relationship('Project', backref=backref('notes', uselist=True, cascade="all, delete-orphan"), uselist=True, lazy='joined')

    def set_ref(self, ref_key, ref_id):
        """
            Using a ref key and ref id set the
            reference to the appropriate resource type.
        """
        if ref_key == 'NETWORK':
            self.network_id = ref_id
        elif ref_key == 'NODE':
            self.node_id = ref_id
        elif ref_key == 'LINK':
            self.link_id = ref_id
        elif ref_key == 'GROUP':
            self.group_id = ref_id
        elif ref_key == 'SCENARIO':
            self.scenario_id = ref_id
        elif ref_key == 'PROJECT':
            self.project_id = ref_id

        else:
            raise HydraError("Ref Key %s not recognised."%ref_key)

    def get_ref_id(self):

        """
            Return the ID of the resource to which this not is attached
        """
        if self.ref_key == 'NETWORK':
            return self.network_id
        elif self.ref_key == 'NODE':
            return self.node_id
        elif self.ref_key == 'LINK':
            return self.link_id
        elif self.ref_key == 'GROUP':
            return self.group_id
        elif self.ref_key == 'SCENARIO':
            return self.scenario_id
        elif self.ref_key == 'PROJECT':
            return self.project_id

    def get_ref(self):
        """
            Return the ID of the resource to which this not is attached
        """
        if self.ref_key == 'NETWORK':
            return self.network
        elif self.ref_key == 'NODE':
            return self.node
        elif self.ref_key == 'LINK':
            return self.link
        elif self.ref_key == 'GROUP':
            return self.group
        elif self.ref_key == 'SCENARIO':
            return self.scenario
        elif self.ref_key == 'PROJECT':
            return self.project


#***************************************************
#Ownership & Permissions
#***************************************************
class ProjectOwner(Base, Inspect):
    """
    """

    __tablename__='tProjectOwner'

    user_id = Column(Integer(), ForeignKey('tUser.user_id'), primary_key=True, nullable=False)
    project_id = Column(Integer(), ForeignKey('tProject.project_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    view = Column(String(1),  nullable=False)
    edit = Column(String(1),  nullable=False)
    share = Column(String(1),  nullable=False)

    user = relationship('User')
    project = relationship('Project', backref=backref('owners', order_by=user_id, uselist=True, cascade="all, delete-orphan"))

class NetworkOwner(Base, Inspect):
    """
    """

    __tablename__='tNetworkOwner'

    user_id = Column(Integer(), ForeignKey('tUser.user_id'), primary_key=True, nullable=False)
    network_id = Column(Integer(), ForeignKey('tNetwork.network_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    view = Column(String(1),  nullable=False)
    edit = Column(String(1),  nullable=False)
    share = Column(String(1),  nullable=False)

    user = relationship('User')
    network = relationship('Network', backref=backref('owners', order_by=user_id, uselist=True, cascade="all, delete-orphan"))

class TemplateOwner(Base, Inspect):
    """
    """

    __tablename__='tTemplateOwner'

    user_id = Column(Integer(), ForeignKey('tUser.user_id'), primary_key=True, nullable=False)
    template_id = Column(Integer(), ForeignKey('tTemplate.template_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    view = Column(String(1),  nullable=False)
    edit = Column(String(1),  nullable=False)
    share = Column(String(1),  nullable=False)

    user = relationship('User')
    template = relationship('Template', backref=backref('owners', order_by=user_id, uselist=True, cascade="all, delete-orphan"))

class DatasetOwner(Base, Inspect):
    """
    """

    __tablename__='tDatasetOwner'

    user_id = Column(Integer(), ForeignKey('tUser.user_id'), primary_key=True, nullable=False)
    dataset_id = Column(Integer(), ForeignKey('tDataset.dataset_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    view = Column(String(1),  nullable=False)
    edit = Column(String(1),  nullable=False)
    share = Column(String(1),  nullable=False)

    user = relationship('User')
    dataset = relationship('Dataset', backref=backref('owners', order_by=user_id, uselist=True, cascade="all, delete-orphan"))

class Perm(Base, Inspect):
    """
    """

    __tablename__='tPerm'

    perm_id = Column(Integer(), primary_key=True, nullable=False)
    perm_code = Column(String(60),  nullable=False)
    perm_name = Column(String(60),  nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    roleperms = relationship('RolePerm', lazy='joined')

class Role(Base, Inspect):
    """
    """

    __tablename__='tRole'

    role_id = Column(Integer(), primary_key=True, nullable=False)
    role_code = Column(String(60),  nullable=False)
    role_name = Column(String(60),  nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    roleperms = relationship('RolePerm', lazy='joined', cascade='all')
    roleusers = relationship('RoleUser', lazy='joined', cascade='all')

    @property
    def permissions(self):
        return set([rp.perm for rp in self.roleperms])


class RolePerm(Base, Inspect):
    """
    """

    __tablename__='tRolePerm'

    perm_id = Column(Integer(), ForeignKey('tPerm.perm_id'), primary_key=True, nullable=False)
    role_id = Column(Integer(), ForeignKey('tRole.role_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    perm = relationship('Perm', lazy='joined')
    role = relationship('Role', lazy='joined')

class RoleUser(Base, Inspect):
    """
    """

    __tablename__='tRoleUser'

    user_id = Column(Integer(), ForeignKey('tUser.user_id'), primary_key=True, nullable=False)
    role_id = Column(Integer(), ForeignKey('tRole.role_id'), primary_key=True, nullable=False)
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))

    user = relationship('User', lazy='joined')
    role = relationship('Role', lazy='joined')

class User(Base, Inspect):
    """
    """

    __tablename__='tUser'

    user_id = Column(Integer(), primary_key=True, nullable=False)
    username = Column(String(60),  nullable=False, unique=True)
    password = Column(String(1000),  nullable=False)
    display_name = Column(String(60),  nullable=False, server_default=text(u"''"))
    last_login = Column(TIMESTAMP())
    last_edit = Column(TIMESTAMP())
    cr_date = Column(TIMESTAMP(),  nullable=False, server_default=text(u'CURRENT_TIMESTAMP'))
    roleusers = relationship('RoleUser', lazy='joined')

    def validate_password(self, password):
        if bcrypt.hashpw(password.encode('utf-8'), self.password.encode('utf-8')) == self.password.encode('utf-8'):
            return True
        return False

    @property
    def permissions(self):
        """Return a set with all permissions granted to the user."""
        perms = set()
        for r in self.roles:
            perms = perms | set(r.permissions)
        return perms

    @property
    def roles(self):
        """Return a set with all roles granted to the user."""
        roles = []
        for ur in self.roleusers:
            roles.append(ur.role)
        return set(roles)


def create_resourcedata_view():
    #These are for creating the resource data view (see bottom of page)
    from sqlalchemy import select
    from sqlalchemy.schema import DDLElement
    from sqlalchemy.sql import table
    from sqlalchemy.ext import compiler
    from model import ResourceAttr, ResourceScenario, Attr, Dataset

    class CreateView(DDLElement):
        def __init__(self, name, selectable):
            self.name = name
            self.selectable = selectable

    class DropView(DDLElement):
        def __init__(self, name):
            self.name = name

    @compiler.compiles(CreateView)
    def compile(element, compiler, **kw):
        return "CREATE VIEW %s AS %s" % (element.name, compiler.sql_compiler.process(element.selectable))

    @compiler.compiles(DropView)
    def compile(element, compiler, **kw):
        return "DROP VIEW %s" % (element.name)

    def view(name, metadata, selectable):
        t = table(name)

        for c in selectable.c:
            c._make_proxy(t)

        CreateView(name, selectable).execute_at('after-create', metadata)
        DropView(name).execute_at('before-drop', metadata)
        return t


    view_qry = select([
        ResourceAttr.resource_attr_id,
        ResourceAttr.attr_id,
        Attr.attr_name,
        ResourceAttr.resource_attr_id,
        ResourceAttr.network_id,
        ResourceAttr.node_id,
        ResourceAttr.link_id,
        ResourceAttr.group_id,
        ResourceScenario.scenario_id,
        ResourceScenario.dataset_id,
        Dataset.data_units,
        Dataset.data_dimen,
        Dataset.data_name,
        Dataset.data_type,
        Dataset.value]).where(ResourceScenario.resource_attr_id==ResourceAttr.attr_id).where(ResourceAttr.attr_id==Attr.attr_id).where(ResourceScenario.dataset_id==Dataset.dataset_id)

    stuff_view = view("vResourceData", Base.metadata, view_qry)
